var Ai2sApp = (function () {
  'use strict';
  function context() {
    var properties = PropertiesService.getScriptProperties(), c, state;
    try {
      c = Ai2sSafety.config(JSON.parse(properties.getProperty('AI2S_CONFIG') || 'null'));
      state = JSON.parse(properties.getProperty('AI2S_STATE') || '{}');
    } catch (error) { Ai2sSafety.fail(error.ai2sCode || 'CONFIG_REQUIRED'); }
    if (!state || typeof state !== 'object' || Array.isArray(state) ||
        (Object.keys(state).length && (!state.configDigest || typeof state.paused !== 'boolean'))) {
      Ai2sSafety.fail('STATE_CORRUPT');
    }
    if (Ai2sSafety.email(Session.getEffectiveUser().getEmail()) !== c.ownerEmail) Ai2sSafety.fail('OWNER_REQUIRED');
    var io = Ai2sGoogle.connection(c, state), digest = io.hash(Ai2sSafety.canonical(c));
    if (state.configDigest && state.configDigest !== digest) Ai2sSafety.fail('CONFIG_CHANGED');
    function save() {
      var json = JSON.stringify(state);
      if (Utilities.newBlob(json).getBytes().length > 8500) Ai2sSafety.fail('STATE_TOO_LARGE');
      properties.setProperty('AI2S_STATE', json);
    }
    if (!state.configDigest) {
      state.configDigest = digest;
      state.ownerEmail = c.ownerEmail;
      state.paused = true;
      save();
    }
    return { c: c, state: state, io: io, save: save, properties: properties };
  }
  function locked(work) {
    var lock = LockService.getScriptLock();
    if (!lock.tryLock(1000)) return { status: 'busy' };
    try {
      var result = work();
      console.log(JSON.stringify(result));
      return result;
    }
    catch (error) {
      var code = Ai2sSafety.code(error);
      console.log(JSON.stringify({ status: 'blocked', code: code }));
      return { status: 'blocked', code: code };
    } finally { lock.releaseLock(); }
  }
  function formRead(x) { return Ai2sGoogle.request('forms', 'forms/' + x.state.form.id); }
  function formWrite(x, form, requests) {
    if (!form.revisionId) Ai2sSafety.fail('REVISION_MISSING');
    Ai2sGoogle.request('forms', 'forms/' + x.state.form.id + ':batchUpdate', 'post', {
      writeControl: { requiredRevisionId: form.revisionId }, requests: requests
    });
  }
  function setupForm(x) {
    var form = FormApp.openById(x.state.form.id);
    if (form.getResponses().length) Ai2sSafety.fail('SETUP_HAS_RESPONSES');
    form.setPublished(false).setTitle(Ai2sQuestionnaire.title).setDescription(Ai2sQuestionnaire.introduction)
      .setAllowResponseEdits(true).setLimitOneResponsePerUser(true).setPublishingSummary(false)
      .setShuffleQuestions(false).setProgressBar(true).setShowLinkToRespondAgain(false);
    if (form.getDestinationId()) Ai2sSafety.fail('AUTO_SHEET_LINKED');
    var compiled = Ai2sFormSchema.compile();
    x.state.items = compiled.mapping;
    x.save();
    var observed = formRead(x), existing = (observed.items || []).map(function (item) { return item.itemId; });
    var expected = compiled.items.map(function (item) { return item.itemId; });
    if (existing.some(function (id) { return expected.indexOf(id) < 0; }) ||
        existing.join(',') !== expected.filter(function (id) { return existing.indexOf(id) >= 0; }).join(',')) {
      Ai2sSafety.fail('FORM_DRIFT');
    }
    var creates = [];
    compiled.items.forEach(function (item, i) {
      if (existing.indexOf(item.itemId) >= 0) return;
      var skeleton = JSON.parse(JSON.stringify(item));
      var choice = skeleton.questionItem && skeleton.questionItem.question.choiceQuestion;
      if (choice) choice.options.forEach(function (o) { delete o.goToSectionId; });
      creates.push({ createItem: { item: skeleton, location: { index: i } } });
    });
    if (creates.length) formWrite(x, observed, creates);
    observed = formRead(x);
    // All page IDs now exist, including forward destinations for multiple-choice routing.
    var updates = compiled.items.map(function (item, i) {
      return { updateItem: { item: item, location: { index: i }, fields: '*' } };
    });
    updates.push({ updateSettings: { settings: { emailCollectionType: 'VERIFIED' },
      updateMask: 'emailCollectionType' } });
    formWrite(x, observed, updates);
    Ai2sFormSchema.validate(formRead(x), x.state.items);
  }
  function setupTemplate(x) {
    var id = x.state.template.id, doc = x.io.readDocument(id), all = Ai2sDocuments.tabs(doc);
    if (all.length === 1) {
      x.io.writeDocument(id, { writeControl: { requiredRevisionId: doc.revisionId }, requests: [
        { updateDocumentTabProperties: {
          tabProperties: { tabId: all[0].tabProperties.tabId, title: 'Profile' }, fields: 'title'
        } }, { addDocumentTab: { tabProperties: { title: 'Member notes' } } }
      ] });
      doc = x.io.readDocument(id);
    }
    var ids = Ai2sDocuments.identify(doc);
    var contents = {
      profile: [
        { style: 'TITLE', text: 'AI2S member profile' },
        { style: 'SUBTITLE', text: 'Self-reported' },
        { style: 'NORMAL_TEXT', text: 'A member response populates this Profile tab.' }
      ],
      notes: [
        { style: 'TITLE', text: 'Member notes' },
        { style: 'NORMAL_TEXT', text: 'Add strengths, qualifications, context, or corrections you want ' +
          'teammates to know. Both tabs are readable by the AI2S team. The intake updates the Profile tab; ' +
          'this Member notes tab is preserved. Avoid confidential information.' }
      ]
    };
    ['profile', 'notes'].forEach(function (key) {
      doc = x.io.readDocument(id);
      var tab = Ai2sDocuments.tabs(doc).filter(function (t) { return t.tabProperties.tabId === ids[key]; })[0];
      var body = tab.documentTab.body.content;
      if (body[body.length - 1].endIndex > 2) return; // Preserve any already initialized text on restart.
      var update = Ai2sDocuments.update(doc, ids[key], contents[key]);
      update.requests.push({ updateDocumentStyle: { tabId: ids[key], documentStyle: {
        marginTop: { magnitude: 72, unit: 'PT' }, marginBottom: { magnitude: 72, unit: 'PT' },
        marginLeft: { magnitude: 72, unit: 'PT' }, marginRight: { magnitude: 72, unit: 'PT' }
      }, fields: 'marginTop,marginBottom,marginLeft,marginRight' } });
      x.io.writeDocument(id, update);
    });
    x.state.templateTabs = ids;
    x.save();
  }
  function setup() {
    return locked(function () {
      var x = context(), c = x.c, state = x.state;
      Ai2sGoogle.root(c);
      if (state.setupDone) return validate(x, false);
      if (state.paused !== true) Ai2sSafety.fail('PAUSE_REQUIRED');
      var ownerRoot = Ai2sGoogle.file('root').id;
      ['sheet', 'form', 'template', 'folder'].forEach(function (kind) {
        var id = Ai2sRecovery.ensure(state, kind, kind, x.io, x.save);
        Ai2sGoogle.audit(id, c, Ai2sSafety.roles(c, kind === 'folder'), false, kind, kind === 'form');
        Ai2sGoogle.parent(id, c.privateRootId, [ownerRoot, c.privateRootId]);
        Ai2sGoogle.grant(id, c, Ai2sSafety.roles(c, kind === 'folder'), kind === 'form');
      });
      setupForm(x);
      setupTemplate(x);
      // Sharing a folder containing only complete profiles is safe; new copies use privateRoot.
      Ai2sGoogle.grant(state.folder.id, c, Ai2sSafety.roles(c, true));
      var titles = { sheet: 'AI2S responses and processing register', template: 'AI2S profile template',
        folder: 'AI2S member profiles' };
      Object.keys(titles).forEach(function (kind) {
        Ai2sGoogle.request('drive', 'files/' + state[kind].id, 'patch', { name: titles[kind] });
      });
      state.setupDone = true;
      x.save();
      return status(x);
    });
  }
  function validate(x, live) {
    var c = x.c, s = x.state;
    if (!s.setupDone) Ai2sSafety.fail('SETUP_REQUIRED');
    Ai2sGoogle.root(c);
    ['sheet', 'form', 'template', 'folder'].forEach(function (kind) {
      var metadata = Ai2sGoogle.audit(s[kind].id, c, Ai2sSafety.roles(c, kind === 'folder'), true,
        kind, kind === 'form');
      if (!metadata.parents || metadata.parents.length !== 1 || metadata.parents[0] !== c.privateRootId) {
        Ai2sSafety.fail('UNEXPECTED_PARENT');
      }
      if (kind !== 'folder' && metadata.writersCanShare !== false) Ai2sSafety.fail('SHARING_ENABLED');
    });
    var f = FormApp.openById(s.form.id), apiForm = formRead(x);
    if (!apiForm.settings || apiForm.settings.emailCollectionType !== 'VERIFIED') Ai2sSafety.fail('EMAIL_NOT_VERIFIED');
    if (!f.hasLimitOneResponsePerUser() || !f.canEditResponse() || f.isPublishingSummary() ||
        f.getShuffleQuestions() || f.isQuiz()) Ai2sSafety.fail('FORM_SETTINGS');
    if (f.getDestinationId()) Ai2sSafety.fail('AUTO_SHEET_LINKED');
    if (f.getTitle() !== Ai2sQuestionnaire.title || f.getDescription() !== Ai2sQuestionnaire.introduction) {
      Ai2sSafety.fail('FORM_DRIFT');
    }
    Ai2sFormSchema.validate(apiForm, s.items);
    Ai2sDocuments.select(x.io.readDocument(s.template.id), s.templateTabs);
    if (live) {
      if (!f.supportsAdvancedResponderPermissions() || !f.isPublished() || !f.isAcceptingResponses()) {
        Ai2sSafety.fail('FORM_NOT_PUBLISHED');
      }
      var published = Ai2sGoogle.permissions(s.form.id, true).filter(function (p) {
        return p.view === 'published' || p.role === 'publishedReader';
      });
      var expected = c.responders.map(function (p) { return p.type + ':' + p.email; }).sort();
      var actual = published.map(function (p) {
        if (p.role !== 'publishedReader' || p.deleted || p.expirationTime) Ai2sSafety.fail('RESPONDER_ACCESS');
        return p.type + ':' + (p.emailAddress || '').toLowerCase();
      }).sort();
      if (Ai2sSafety.canonical(expected) !== Ai2sSafety.canonical(actual)) Ai2sSafety.fail('RESPONDER_ACCESS');
    }
    if (!s.paused) {
      var triggers = ScriptApp.getProjectTriggers();
      ['ai2sOnSubmit', 'ai2sReconcile'].forEach(function (handler) {
        var found = triggers.filter(function (t) { return t.getHandlerFunction() === handler; });
        if (found.length !== 1 || !s.triggers || found[0].getUniqueId() !== s.triggers[handler] ||
            (handler === 'ai2sOnSubmit' && (found[0].getTriggerSourceId() !== s.form.id ||
              found[0].getEventType() !== ScriptApp.EventType.ON_FORM_SUBMIT)) ||
            (handler === 'ai2sReconcile' && found[0].getEventType() !== ScriptApp.EventType.CLOCK)) {
          Ai2sSafety.fail('TRIGGER_DRIFT');
        }
      });
    }
    return { status: 'validated', paused: s.paused };
  }
  function status(x) {
    var s = x.state, result = { status: s.setupDone ? 'prepared' : 'setup-pending', paused: s.paused,
      form: s.form && s.form.id, sheet: s.sheet && s.sheet.id,
      template: s.template && s.template.id, profilesFolder: s.folder && s.folder.id };
    return result;
  }
  function install() {
    return locked(function () {
      var x = context(), s = x.state;
      validate(x, true);
      if (!s.paused) Ai2sSafety.fail('PAUSE_REQUIRED');
      s.triggers = s.triggers || {};
      ['ai2sOnSubmit', 'ai2sReconcile'].forEach(function (handler) {
        var found = ScriptApp.getProjectTriggers().filter(function (t) { return t.getHandlerFunction() === handler; });
        if (found.length > 1) Ai2sSafety.fail('TRIGGER_DRIFT');
        if (found.length) {
          var t = found[0];
          if (handler === 'ai2sOnSubmit' && (t.getTriggerSourceId() !== s.form.id ||
              t.getEventType() !== ScriptApp.EventType.ON_FORM_SUBMIT)) Ai2sSafety.fail('TRIGGER_DRIFT');
          if (handler === 'ai2sReconcile' && t.getEventType() !== ScriptApp.EventType.CLOCK) Ai2sSafety.fail('TRIGGER_DRIFT');
          if (s.triggers[handler] !== t.getUniqueId() && s.triggerAttempt !== handler) Ai2sSafety.fail('TRIGGER_DRIFT');
          s.triggers[handler] = t.getUniqueId();
        } else {
          s.triggerAttempt = handler;
          x.save();
          var builder = ScriptApp.newTrigger(handler);
          var trigger = handler === 'ai2sOnSubmit' ? builder.forForm(s.form.id).onFormSubmit().create() :
            builder.timeBased().everyMinutes(15).create();
          s.triggers[handler] = trigger.getUniqueId();
        }
        delete s.triggerAttempt;
        x.save();
      });
      validate(x, true);
      s.paused = false;
      x.save();
      return { status: 'enabled' };
    });
  }
  function pause() {
    return locked(function () {
      // Pause remains available even when changed configuration prevents normal validation.
      var p = PropertiesService.getScriptProperties(), s = JSON.parse(p.getProperty('AI2S_STATE') || '{}');
      if (!s.ownerEmail || Ai2sSafety.email(Session.getEffectiveUser().getEmail()) !== s.ownerEmail) {
        Ai2sSafety.fail('OWNER_REQUIRED');
      }
      s.paused = true;
      p.setProperty('AI2S_STATE', JSON.stringify(s));
      ScriptApp.getProjectTriggers().forEach(function (t) {
        if (['ai2sOnSubmit', 'ai2sReconcile'].indexOf(t.getHandlerFunction()) >= 0) ScriptApp.deleteTrigger(t);
      });
      s.triggers = {};
      delete s.triggerAttempt;
      p.setProperty('AI2S_STATE', JSON.stringify(s));
      return { status: 'paused' };
    });
  }
  function run(responseId, eventEmail, eventFormId) {
    return locked(function () {
      var x = context();
      if (x.state.paused) return { status: 'paused' };
      validate(x, true);
      if (eventFormId && eventFormId !== x.state.form.id) Ai2sSafety.fail('EVENT_SOURCE');
      if (responseId) return Ai2sPipeline.process(x.io, responseId, eventEmail);
      var ids = x.io.responseIds(), start = x.state.cursor || 0, deadline = Date.now() + 240000;
      var result = { status: 'reconciled', processed: 0, pending: 0, blocked: 0 };
      for (var n = 0; n < ids.length && Date.now() < deadline && n < 100; n++) {
        var index = (start + n) % ids.length;
        var output = Ai2sPipeline.process(x.io, ids[index]);
        result.processed++;
        if (output.status === 'pending') result.pending++;
        if (output.status === 'blocked') result.blocked++;
        x.state.cursor = (index + 1) % ids.length;
        x.save();
      }
      return result;
    });
  }
  function recover() {
    return locked(function () {
      var x = context(), r;
      if (!x.state.paused) Ai2sSafety.fail('PAUSE_REQUIRED');
      try { r = JSON.parse(x.properties.getProperty('AI2S_RECOVERY') || 'null'); }
      catch (_) { Ai2sSafety.fail('RECOVERY_MISMATCH'); }
      if (!r) Ai2sSafety.fail('RECOVERY_MISMATCH');
      if (r.action === 'resolveIdentity') {
        validate(x, true);
        Ai2sPipeline.resolveIdentity(x.io, r.responseId, r.originalEmail);
      } else if (r.action === 'confirmAbsent' && r.confirmedAbsent === true) {
        Ai2sGoogle.root(x.c);
        if (['sheet', 'form', 'template', 'folder'].indexOf(r.asset) >= 0) {
          Ai2sRecovery.confirmAbsent(x.state[r.asset], r.token, x.io, x.save);
        } else if (r.asset === 'profile') {
          validate(x, true);
          var key = 'member:' + Ai2sSafety.email(r.memberEmail), m = x.io.get(key);
          if (!m) Ai2sSafety.fail('RECOVERY_MISMATCH');
          Ai2sRecovery.confirmAbsent(m.file, r.token, x.io, function () { x.io.put(key, m); });
        } else Ai2sSafety.fail('RECOVERY_MISMATCH');
      } else Ai2sSafety.fail('RECOVERY_MISMATCH');
      x.properties.deleteProperty('AI2S_RECOVERY');
      return { status: 'recovered', processing: 'paused' };
    });
  }
  return { setup: setup, install: install, pause: pause, run: run, recover: recover,
    locked: locked, context: context, validate: validate, status: status };
}());

function ai2sSetup() { return Ai2sApp.setup(); }
function ai2sValidate() { return Ai2sApp.locked(function () { return Ai2sApp.validate(Ai2sApp.context(), true); }); }
function ai2sStatus() { return Ai2sApp.locked(function () { return Ai2sApp.status(Ai2sApp.context()); }); }
function ai2sInstallTriggers() { return Ai2sApp.install(); }
function ai2sPause() { return Ai2sApp.pause(); }
function ai2sReconcile() { return Ai2sApp.run(); }
function ai2sRetry() { return Ai2sApp.run(); }
function ai2sRecover() { return Ai2sApp.recover(); }
function ai2sOnSubmit(event) {
  if (!event || !event.response || !event.source) return { status: 'blocked', code: 'EVENT_REQUIRED' };
  try { return Ai2sApp.run(event.response.getId(), event.response.getRespondentEmail(), event.source.getId()); }
  catch (_) { return { status: 'blocked', code: 'EVENT_INVALID' }; }
}
