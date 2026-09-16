'use strict';

// In-memory API emulator: exercises the real Apps Script entrypoints and REST adapter.
// It models atomic revisions, inherited ACLs, literal Sheet writes and lost responses.
// It is not evidence about Google's live implementation or institutional policy.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const plain = (value) => JSON.parse(JSON.stringify(value));
const time = '2026-09-16T18:00:00.000Z';
const config = {
  ownerEmail: 'owner@example.invalid', coordinatorEmails: ['coordinator@example.invalid'],
  privateRootId: 'private-root', teamReaders: [{ type: 'group', email: 'team@example.invalid' }],
  responders: [{ type: 'group', email: 'pilot@example.invalid' }]
};
const fixtures = JSON.parse(fs.readFileSync(path.join(__dirname, '../fixtures/members.json'), 'utf8'));

function createFake() {
  const f = {
    config: plain(config), files: {}, forms: {}, docs: {}, sheets: {}, properties: {},
    calls: [], logs: [], triggers: [], faults: [], counter: 0, locked: false,
    effectiveEmail: config.ownerEmail, onRequest: null, onSetProperty: null
  };
  f.properties.AI2S_CONFIG = JSON.stringify(config);
  f.file = (kind, name, parents = ['owner-root']) => {
    const id = 'asset-' + (++f.counter);
    f.files[id] = { id, name, mimeType: 'application/vnd.google-apps.' + kind,
      owners: [{ emailAddress: config.ownerEmail }], parents, writersCanShare: true,
      acl: [{ id: 'owner-acl', type: 'user', emailAddress: config.ownerEmail, role: 'owner' }] };
    return id;
  };
  const root = f.file('folder', 'Owner root', []);
  f.files['owner-root'] = { ...f.files[root], id: 'owner-root' };
  delete f.files[root];
  const privateRoot = f.file('folder', 'Private root');
  f.files['private-root'] = { ...f.files[privateRoot], id: 'private-root' };
  delete f.files[privateRoot];
  f.acl = (id) => {
    const result = {};
    const metadata = f.files[id];
    for (const parent of metadata.parents) {
      for (const p of f.acl(parent)) result[p.type + ':' + p.emailAddress + ':' + (p.view || '')] = p;
    }
    for (const p of metadata.acl) result[p.type + ':' + p.emailAddress + ':' + (p.view || '')] = p;
    return Object.values(result);
  };
  f.tab = (id, title, text = '') => ({ tabProperties: { tabId: id, title }, documentTab: {
    body: { content: [{ sectionBreak: {}, endIndex: 1 }, { startIndex: 1,
      endIndex: text.length + 2, paragraph: { elements: [{ textRun: { content: text + '\n' } }] } }] }
  } });
  f.docText = (id, tabId) => {
    const tab = f.docs[id].tabs.find((t) => t.tabProperties.tabId === tabId);
    return tab.documentTab.body.content.filter((c) => c.paragraph)
      .flatMap((c) => c.paragraph.elements).map((e) => e.textRun?.content || '').join('');
  };
  f.applyDoc = (id, body) => {
    const doc = f.docs[id];
    assert.ok(body.writeControl?.requiredRevisionId, 'Every document write requires a revision');
    if (body.writeControl.requiredRevisionId !== doc.revisionId) return { errorStatus: 400 };
    const updated = plain(doc);
    for (const r of body.requests) {
      const name = Object.keys(r)[0], value = r[name];
      if (name === 'addDocumentTab') {
        updated.tabs.push(f.tab('tab-' + (++f.counter), value.tabProperties.title));
        continue;
      }
      const tabId = value.range?.tabId || value.location?.tabId || value.tabProperties?.tabId || value.tabId;
      assert.ok(tabId, 'Every document content/style request explicitly targets a tab');
      const tab = updated.tabs.find((t) => t.tabProperties.tabId === tabId);
      assert.ok(tab, 'Target tab exists');
      if (name === 'updateDocumentTabProperties') tab.tabProperties.title = value.tabProperties.title;
      else if (name === 'deleteContentRange') {
        const last = tab.documentTab.body.content.at(-1);
        assert.equal(value.range.startIndex, 1);
        assert.equal(value.range.endIndex, last.endIndex - 1, 'Keep terminal newline');
        tab.documentTab = f.tab(tabId, '').documentTab;
      } else if (name === 'insertText') {
        assert.equal(value.location.index, 1);
        tab.documentTab = f.tab(tabId, '', value.text).documentTab;
      } else if (['updateTextStyle', 'updateParagraphStyle', 'updateDocumentStyle'].includes(name)) {
        (tab.testStyles ||= []).push(plain(r));
      } else assert.fail('Unsupported document operation: ' + name);
    }
    updated.revisionId = 'revision-' + (++f.counter);
    f.docs[id] = updated;
    return {};
  };
  f.formObject = (id) => {
    const handle = { getId: () => id, getResponses: () => Object.values(f.forms[id].responses) };
    const setters = {
      setPublished: 'published', setTitle: 'title', setDescription: 'description',
      setAllowResponseEdits: 'edits', setLimitOneResponsePerUser: 'limit',
      setPublishingSummary: 'summary', setShuffleQuestions: 'shuffle', setProgressBar: 'progress',
      setShowLinkToRespondAgain: 'again'
    };
    for (const [name, key] of Object.entries(setters)) handle[name] = (value) => {
      f.forms[id][key] = value;
      return handle;
    };
    const getters = {
      getTitle: 'title', getDescription: 'description', canEditResponse: 'edits',
      hasLimitOneResponsePerUser: 'limit', isPublishingSummary: 'summary',
      getShuffleQuestions: 'shuffle', isQuiz: 'quiz', getDestinationId: 'destination',
      isPublished: 'published', isAcceptingResponses: 'accepting'
    };
    for (const [name, key] of Object.entries(getters)) handle[name] = () => f.forms[id][key];
    handle.supportsAdvancedResponderPermissions = () => true;
    return handle;
  };
  f.createForm = (name, published) => {
    assert.equal(published, false, 'Forms start unpublished');
    const id = f.file('form', name);
    f.forms[id] = { formId: id, title: name, published, responses: {}, items: [],
      revisionId: 'form-rev-' + (++f.counter), settings: {} };
    const fault = f.faults.find((e) => e.formCreate);
    if (fault) { f.faults.splice(f.faults.indexOf(fault), 1); throw new Error('private form provider error'); }
    return f.formObject(id);
  };
  function route(url, options, body) {
    const parts = url.pathname.split('/').filter(Boolean);
    const method = options.method;
    if (url.hostname === 'www.googleapis.com') {
      const rest = parts.slice(2), id = rest[1] === 'root' ? 'owner-root' : rest[1];
      if (rest[0] !== 'files') assert.fail('Unexpected Drive resource');
      if (rest.length === 1 && method === 'get') {
        const q = url.searchParams.get('q');
        const name = q.match(/name = '([^']+)'/)?.[1];
        const token = q.match(/value='([^']+)'/)?.[1];
        const mime = q.match(/mimeType = '([^']+)'/)?.[1];
        return { files: Object.values(f.files).filter((v) => !v.trashed && v.mimeType === mime &&
          (name ? v.name === name : v.appProperties?.ai2sOperation === token)).map((v) => ({ id: v.id })) };
      }
      if (rest.length === 1 && method === 'post') {
        assert.equal(body.mimeType, 'application/vnd.google-apps.folder');
        return { id: f.file('folder', body.name, body.parents) };
      }
      if (!f.files[id]) return { errorStatus: 404 };
      if (rest[2] === 'permissions') {
        if (method === 'get') return { permissions: f.acl(id).filter((p) =>
          url.searchParams.has('includePermissionsForView') || !p.view) };
        assert.equal(url.searchParams.get('sendNotificationEmail'), 'false');
        f.files[id].acl.push({ ...body, id: 'acl-' + (++f.counter) });
        return { id: f.files[id].acl.at(-1).id };
      }
      if (rest[2] === 'copy') {
        const copy = f.file('document', body.name, body.parents);
        Object.assign(f.files[copy], body);
        f.docs[copy] = plain(f.docs[id]);
        f.docs[copy].documentId = copy;
        f.docs[copy].revisionId = 'copy-rev-' + (++f.counter);
        return { id: copy };
      }
      if (method === 'get') return f.files[id];
      if (method === 'patch') {
        Object.assign(f.files[id], body);
        if (url.searchParams.has('addParents')) f.files[id].parents = [url.searchParams.get('addParents')];
        return f.files[id];
      }
    }
    if (url.hostname === 'docs.googleapis.com') {
      if (parts.length === 2) {
        const id = f.file('document', body.title);
        f.docs[id] = { documentId: id, revisionId: 'doc-rev-' + (++f.counter), tabs: [f.tab('initial', 'Tab 1')] };
        return { documentId: id };
      }
      const id = parts[2].split(':')[0];
      if (!f.docs[id]) return { errorStatus: 404 };
      if (method === 'get') {
        assert.equal(url.searchParams.get('includeTabsContent'), 'true');
        return f.docs[id];
      }
      return f.applyDoc(id, body);
    }
    if (url.hostname === 'forms.googleapis.com') {
      const id = parts[2].split(':')[0], form = f.forms[id];
      if (!form) return { errorStatus: 404 };
      if (parts[3] === 'responses') {
        return parts[4] ? form.responses[parts[4]] || { errorStatus: 404 } : { responses: Object.values(form.responses) };
      }
      if (method === 'get') return form;
      if (body.writeControl.requiredRevisionId !== form.revisionId) return { errorStatus: 400 };
      for (const r of body.requests) {
        if (r.createItem) {
          if (form.items.some((i) => i.itemId === r.createItem.item.itemId)) return { errorStatus: 400 };
          form.items.splice(r.createItem.location.index, 0, r.createItem.item);
        } else if (r.updateItem) {
          assert.equal(form.items[r.updateItem.location.index].itemId, r.updateItem.item.itemId);
          const q = r.updateItem.item.questionItem?.question?.choiceQuestion;
          for (const o of q?.options || []) {
            assert.equal(typeof o.value, 'string', 'Form option value required, including Other');
            if (o.goToSectionId) assert.ok(form.items.some((i) => i.itemId === o.goToSectionId && i.pageBreakItem));
          }
          form.items[r.updateItem.location.index] = r.updateItem.item;
        } else if (r.updateSettings) Object.assign(form.settings, r.updateSettings.settings);
        else assert.fail('Unexpected Forms request');
      }
      form.revisionId = 'form-rev-' + (++f.counter);
      return {};
    }
    if (url.hostname === 'sheets.googleapis.com') {
      if (parts.length === 2) {
        const id = f.file('spreadsheet', body.properties.title);
        f.sheets[id] = { Register: [], Responses: [], definitions: body.sheets };
        for (const s of f.sheets[id].definitions) s.properties.gridProperties.rowCount = 1000;
        return { spreadsheetId: id };
      }
      const id = parts[2].split(':')[0], sheet = f.sheets[id];
      if (parts[3] === 'values') {
        const range = decodeURIComponent(parts[4]), tab = range.split('!')[0];
        if (method === 'get') return { values: sheet[tab] };
        assert.equal(url.searchParams.get('valueInputOption'), 'RAW', 'Respondent cells must be literal');
        const row = Number(range.match(/!A(\d+)/)[1]) - 1;
        sheet[tab][row] = body.values[0];
        return {};
      }
      if (method === 'get') return { sheets: sheet.definitions };
      const op = body.requests[0].appendDimension;
      sheet.definitions.find((s) => s.properties.sheetId === op.sheetId).properties.gridProperties.rowCount += op.length;
      return {};
    }
    assert.fail('Unexpected request: ' + url.href);
  }
  const globals = {
    console: { log: (message) => f.logs.push(message) },
    PropertiesService: { getScriptProperties: () => ({
      getProperty: (key) => f.properties[key] || null,
      setProperty: (key, value) => { if (f.onSetProperty) f.onSetProperty(key, value); f.properties[key] = value; },
      deleteProperty: (key) => { delete f.properties[key]; }
    }) },
    Session: { getEffectiveUser: () => ({ getEmail: () => f.effectiveEmail }) },
    LockService: { getScriptLock: () => ({
      tryLock: () => { if (f.locked) return false; f.locked = true; return true; },
      releaseLock: () => { f.locked = false; }
    }) },
    Utilities: {
      getUuid: () => 'operation-' + (++f.counter),
      newBlob: (s) => ({ getBytes: () => [...Buffer.from(s)] }),
      DigestAlgorithm: { SHA_256: 'sha256' }, Charset: { UTF_8: 'utf8' },
      computeDigest: (algorithm, text) => [...crypto.createHash(algorithm).update(text).digest()]
    },
    ScriptApp: {
      getOAuthToken: () => 'synthetic-token', EventType: { ON_FORM_SUBMIT: 'submit', CLOCK: 'clock' },
      getProjectTriggers: () => f.triggers,
      deleteTrigger: (t) => { f.triggers = f.triggers.filter((v) => v !== t); },
      newTrigger: (handler) => {
        let source = null, kind = null, interval;
        const builder = {
          forForm: (id) => { source = id; return builder; },
          onFormSubmit: () => { kind = 'submit'; return builder; },
          timeBased: () => { kind = 'clock'; return builder; },
          everyMinutes: (n) => { interval = n; return builder; },
          create: () => {
            if (kind === 'clock') assert.equal(interval, 15);
            const id = 'trigger-' + (++f.counter);
            const trigger = { getHandlerFunction: () => handler, getTriggerSourceId: () => source,
              getEventType: () => kind, getUniqueId: () => id };
            f.triggers.push(trigger);
            if (f.loseTrigger) { f.loseTrigger = false; throw new Error('private trigger error'); }
            return trigger;
          }
        };
        return builder;
      }
    },
    FormApp: { create: f.createForm, openById: f.formObject },
    UrlFetchApp: { fetch: (address, options) => {
      const url = new URL(address), body = options.payload ? JSON.parse(options.payload) : null;
      f.calls.push({ url: address, method: options.method, body, options: plain(options) });
      if (f.onRequest) f.onRequest(url, options, body);
      const fault = f.faults.find((entry) => entry.match?.(url, options, body));
      if (fault) f.faults.splice(f.faults.indexOf(fault), 1);
      let result;
      if (!fault || fault.after) result = route(url, options, body);
      if (fault) {
        if (!fault.status) throw new Error('PRIVATE RESPONDENT DATA IN PROVIDER ERROR');
        result = { errorStatus: fault.status };
      }
      return { getResponseCode: () => result.errorStatus || 200,
        getContentText: () => JSON.stringify(result.errorStatus ? { error: 'PRIVATE RESPONSE CONTENT' } : result) };
    } }
  };
  f.context = vm.createContext(globals);
  for (const name of fs.readdirSync(path.join(__dirname, '../src')).filter((n) => n.endsWith('.gs'))) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../src', name), 'utf8'), f.context, { filename: name });
  }
  f.state = () => JSON.parse(f.properties.AI2S_STATE);
  f.publish = () => {
    const formId = f.state().form.id;
    f.forms[formId].published = true;
    f.forms[formId].accepting = true;
    f.files[formId].acl.push({ id: 'responder-acl', type: 'group', role: 'publishedReader',
      emailAddress: 'pilot@example.invalid', view: 'published' });
  };
  f.ready = () => {
    assert.equal(f.context.ai2sSetup().status, 'prepared', f.logs.join('\n'));
    f.publish();
    assert.equal(f.context.ai2sInstallTriggers().status, 'enabled', f.logs.join('\n'));
  };
  f.submit = (name = 'accountant', id = 'response-1', email = 'member@example.invalid') => {
    const mapping = f.state().items;
    const source = fixtures.find((v) => v.case === name).answers;
    const answers = {};
    for (const [key, value] of Object.entries(source)) answers[mapping[key].questionId] = {
      questionId: mapping[key].questionId,
      textAnswers: { answers: (Array.isArray(value) ? value : [value]).map((v) => ({ value: v })) }
    };
    const response = { responseId: id, respondentEmail: email, createTime: time, lastSubmittedTime: time, answers };
    f.forms[f.state().form.id].responses[id] = response;
    return response;
  };
  f.event = (id = 'response-1', email = 'member@example.invalid') => ({
    response: { getId: () => id, getRespondentEmail: () => email },
    source: { getId: () => f.state().form.id }
  });
  f.record = (key) => {
    const row = f.sheets[f.state().sheet.id].Register.find((r) => r[0] === key);
    return row ? JSON.parse(row[1]) : null;
  };
  f.member = () => f.record('member:member@example.invalid');
  return f;
}
module.exports = { createFake, plain, fixtures, config, time };
