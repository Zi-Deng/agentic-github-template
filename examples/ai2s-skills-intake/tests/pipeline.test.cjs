'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createFake, plain, fixtures, config } = require('./google-fake.cjs');

function edit(f, response, key, value) {
  const id = f.state().items[key].questionId;
  response.answers[id] = { questionId: id, textAnswers: { answers: [{ value }] } };
  response.lastSubmittedTime = '2026-09-16T19:00:00.000Z';
}
function notes(f) { return plain(f.docs[f.member().file.id].tabs.find((t) => t.tabProperties.title === 'Member notes')); }
function decorateNotes(f) {
  const tab = f.docs[f.member().file.id].tabs.find((t) => t.tabProperties.title === 'Member notes');
  tab.documentTab.body.content.push({ table: { rows: 1, columns: 1, tableRows: [
    { tableCells: [{ content: [{ paragraph: { elements: [{ textRun: { content: 'Member correction',
      textStyle: { bold: true, link: { url: 'https://example.org/member-note' } } } }] } }] }] }
  ] }, endIndex: 1000 });
  f.docs[f.member().file.id].revisionId += '-member-edit';
  return notes(f);
}

test('setup is repeatable with persisted IDs, verified email, routing and restricted raw assets', () => {
  const f = createFake();
  assert.equal(f.context.ai2sSetup().status, 'prepared', f.logs.join('\n'));
  const before = f.state(), assets = Object.keys(f.files);
  assert.equal(f.context.ai2sSetup().status, 'validated');
  assert.deepEqual(Object.keys(f.files), assets);
  assert.deepEqual(f.state(), before);
  const form = f.forms[before.form.id];
  assert.equal(form.settings.emailCollectionType, 'VERIFIED');
  assert.equal(form.summary, false);
  assert.equal(form.published, false);
  assert.equal(form.limit, true);
  assert.equal(form.edits, true);
  const route = form.items.find((i) => i.itemId === before.items.moreDetail.itemId).questionItem.question;
  const destination = route.choiceQuestion.options.find((o) => o.value === 'Finish core').goToSectionId;
  assert.equal(form.items.find((i) => i.itemId === destination).title, 'Profile visibility');
  for (const asset of ['sheet', 'template', 'form']) {
    assert.deepEqual(f.acl(before[asset].id).map((p) => p.emailAddress).sort(),
      ['coordinator@example.invalid', 'owner@example.invalid']);
  }
  assert.ok(f.acl(before.folder.id).some((p) => p.emailAddress === 'team@example.invalid' && p.role === 'reader'));
  assert.equal(Object.keys(before.items).length, 26);
});

for (const fixture of fixtures) test('real entrypoint and adapters process synthetic ' + fixture.case, () => {
  const f = createFake();
  f.ready();
  f.submit(fixture.case);
  assert.equal(f.context.ai2sOnSubmit(f.event()).status, 'complete', f.logs.join('\n'));
  const member = f.member(), text = f.docText(member.file.id, member.tabs.profile);
  assert.ok(text.includes(fixture.answers.strength1));
  assert.ok(text.includes('self-reported'));
  assert.equal(f.files[member.file.id].parents[0], f.state().folder.id);
  assert.ok(f.acl(member.file.id).some((p) => p.emailAddress === 'member@example.invalid' && p.role === 'writer'));
  assert.ok(f.acl(member.file.id).some((p) => p.emailAddress === 'team@example.invalid' && p.role === 'reader'));
  assert.equal(f.files[member.file.id].writersCanShare, false);
  assert.equal(f.files[member.file.id].name, 'AI2S — ' + fixture.answers.name);
  const calls = f.calls, copyIndex = calls.findIndex((c) => c.url.includes('/copy'));
  const populationIndex = calls.findIndex((c, i) => i > copyIndex && c.url.includes(member.file.id + ':batchUpdate'));
  const sharingIndex = calls.findIndex((c, i) => i > copyIndex && c.url.includes(member.file.id + '/permissions') && c.method === 'post');
  assert.ok(populationIndex > copyIndex && sharingIndex > populationIndex, 'Populate before any new sharing');
});

test('edited responses update one URL, preserve styled notes and ignore stale event answers', () => {
  const f = createFake(); f.ready(); const response = f.submit();
  f.context.ai2sOnSubmit(f.event());
  const fileId = f.member().file.id, beforeNotes = decorateNotes(f);
  edit(f, response, 'strength1', 'Procurement workflow design');
  const stale = f.event();
  stale.response.getItemResponses = () => { throw new Error('Event answers must not be read'); };
  assert.equal(f.context.ai2sOnSubmit(stale).status, 'complete');
  assert.equal(f.member().file.id, fileId);
  assert.deepEqual(notes(f), beforeNotes);
  assert.match(f.docText(fileId, f.member().tabs.profile), /Procurement workflow design/);
  assert.doesNotMatch(f.docText(fileId, f.member().tabs.profile), /Grant budget reconciliation/);
  assert.equal(f.context.ai2sOnSubmit(stale).status, 'unchanged');
});

test('duplicate events avoid rewrites and an overlapping execution does not acquire the lock', () => {
  const f = createFake(); f.ready(); f.submit();
  let concurrent;
  f.onRequest = (url) => {
    if (url.pathname.endsWith('/copy')) concurrent = f.context.ai2sOnSubmit(f.event());
  };
  assert.equal(f.context.ai2sOnSubmit(f.event()).status, 'complete');
  assert.equal(concurrent.status, 'busy');
  const writes = f.calls.filter((c) => c.url.includes(':batchUpdate') && c.url.includes('docs.google')).length;
  const allWrites = f.calls.filter((c) => c.method !== 'get').length;
  assert.equal(f.context.ai2sOnSubmit(f.event()).status, 'unchanged');
  assert.equal(f.calls.filter((c) => c.method !== 'get').length, allWrites);
  assert.equal(f.calls.filter((c) => c.url.includes(':batchUpdate') && c.url.includes('docs.google')).length, writes);
  assert.equal(f.calls.filter((c) => c.url.includes('/copy')).length, 1);
});

test('a lost profile-copy response is reconciled by its atomic marker without another copy', () => {
  const f = createFake(); f.ready(); f.submit();
  f.faults.push({ match: (u) => u.pathname.endsWith('/copy'), after: true });
  assert.equal(f.context.ai2sOnSubmit(f.event()).status, 'pending');
  assert.equal(f.member().file.status, 'attempted');
  assert.equal(f.context.ai2sRetry().pending, 0, f.logs.join('\n'));
  assert.equal(f.member().status, 'complete');
  assert.equal(f.calls.filter((c) => c.url.includes('/copy')).length, 1);
});

test('a lost register-write response reloads durable rows and retains the one created profile', () => {
  const f = createFake(); f.ready(); f.submit();
  f.faults.push({ match: (u, o, body) => u.hostname === 'sheets.googleapis.com' &&
    o.method === 'put' && body.values?.[0]?.[0] === 'member:member@example.invalid' &&
    JSON.parse(body.values[0][1]).file?.status === 'created', after: true });
  assert.equal(f.context.ai2sOnSubmit(f.event()).status, 'pending');
  const id = f.member().file.id;
  assert.equal(f.context.ai2sRetry().pending, 0, f.logs.join('\n'));
  assert.equal(f.member().status, 'complete');
  assert.equal(f.member().file.id, id);
  assert.equal(f.calls.filter((c) => c.url.includes('/copy')).length, 1);
  assert.equal(f.context.ai2sOnSubmit(f.event()).status, 'unchanged');
});

test('an unobserved copy remains pending until explicit confirmed-absence recovery', () => {
  const f = createFake(); f.ready(); f.submit();
  f.faults.push({ match: (u) => u.pathname.endsWith('/copy') });
  assert.equal(f.context.ai2sOnSubmit(f.event()).status, 'pending');
  assert.equal(f.context.ai2sRetry().pending, 1);
  assert.equal(f.member().error, 'CREATION_UNCERTAIN');
  assert.equal(f.calls.filter((c) => c.url.includes('/copy')).length, 1);
  f.context.ai2sPause();
  f.properties.AI2S_RECOVERY = JSON.stringify({ action: 'confirmAbsent', asset: 'profile',
    memberEmail: 'member@example.invalid', token: f.member().file.token, confirmedAbsent: true });
  assert.equal(f.context.ai2sRecover().status, 'recovered');
  f.context.ai2sInstallTriggers();
  assert.equal(f.context.ai2sRetry().pending, 0);
  assert.equal(f.member().status, 'complete');
});

test('setup reconciles lost native form creation without duplicating assets', () => {
  const f = createFake();
  f.faults.push({ formCreate: true });
  assert.equal(f.context.ai2sSetup().status, 'blocked');
  const formIds = Object.keys(f.forms);
  assert.equal(formIds.length, 1);
  assert.equal(f.context.ai2sSetup().status, 'prepared', f.logs.join('\n'));
  assert.deepEqual(Object.keys(f.forms), formIds);
});

test('setup resumes after sharing its empty profiles folder but before final state save', () => {
  const f = createFake(); let failed = false;
  f.onSetProperty = (key, value) => {
    if (key === 'AI2S_STATE' && JSON.parse(value).setupDone && !failed) {
      failed = true; throw new Error('state write interrupted');
    }
  };
  assert.equal(f.context.ai2sSetup().status, 'blocked');
  const count = Object.keys(f.files).length;
  assert.equal(f.context.ai2sSetup().status, 'prepared', f.logs.join('\n'));
  assert.equal(Object.keys(f.files).length, count);
});

test('creation ambiguity never selects an arbitrary profile', () => {
  const f = createFake(); f.ready(); f.submit();
  f.faults.push({ match: (u) => u.pathname.endsWith('/copy'), after: true });
  f.context.ai2sOnSubmit(f.event());
  const original = Object.values(f.files).find((v) => v.appProperties);
  f.files.duplicate = { ...plain(original), id: 'duplicate' };
  assert.equal(f.context.ai2sRetry().pending, 1);
  assert.equal(f.member().error, 'CREATION_AMBIGUOUS');
  assert.equal(f.calls.filter((c) => c.url.includes('/copy')).length, 1);
});

test('lost document-write response and partial sharing are recoverable without losing notes', () => {
  const f = createFake(); f.ready(); const response = f.submit();
  f.context.ai2sOnSubmit(f.event());
  const before = decorateNotes(f), id = f.member().file.id;
  edit(f, response, 'role', 'Updated role');
  f.faults.push({ match: (u) => u.hostname === 'docs.googleapis.com' && u.pathname.includes(id + ':batchUpdate'), after: true });
  assert.equal(f.context.ai2sOnSubmit(f.event()).status, 'pending');
  assert.deepEqual(notes(f), before);
  assert.equal(f.context.ai2sRetry().pending, 0);
  assert.deepEqual(notes(f), before);
  assert.equal(f.member().file.id, id);
  const g = createFake(); g.ready(); g.submit();
  g.faults.push({ match: (u, o, b) => u.pathname.endsWith('/permissions') &&
    o.method === 'post' && b.emailAddress === 'member@example.invalid', after: true });
  assert.equal(g.context.ai2sOnSubmit(g.event()).status, 'pending');
  assert.equal(g.context.ai2sRetry().pending, 0);
  assert.equal(g.calls.filter((c) => c.url.includes('/copy')).length, 1);
});

test('a concurrent document edit rejects the old revision and the next retry preserves it', () => {
  const f = createFake(); f.ready(); const response = f.submit(); f.context.ai2sOnSubmit(f.event());
  edit(f, response, 'role', 'New role');
  let snapshot, changed = false;
  f.onRequest = (url) => {
    if (!changed && url.hostname === 'docs.googleapis.com' && url.pathname.includes(':batchUpdate')) {
      changed = true; snapshot = decorateNotes(f);
    }
  };
  assert.equal(f.context.ai2sOnSubmit(f.event()).status, 'pending');
  assert.equal(f.member().error, 'REQUEST_REJECTED');
  assert.deepEqual(notes(f), snapshot);
  assert.equal(f.context.ai2sRetry().pending, 0);
  assert.deepEqual(notes(f), snapshot);
});

test('response changes during creation stay private until current answers are populated', () => {
  const f = createFake(); f.ready(); const response = f.submit();
  let changed = false;
  f.onRequest = (url) => {
    if (!changed && url.pathname.endsWith('/copy')) { changed = true; edit(f, response, 'role', 'Latest role'); }
  };
  assert.equal(f.context.ai2sOnSubmit(f.event()).status, 'pending');
  const id = f.member().file.id;
  assert.equal(f.files[id].parents[0], 'private-root');
  assert.ok(!f.acl(id).some((p) => p.emailAddress === 'team@example.invalid'));
  assert.equal(f.context.ai2sRetry().pending, 0);
  assert.match(f.docText(id, f.member().tabs.profile), /Latest role/);
});

test('changed account is sticky-blocked; owner can restore only the original binding', () => {
  const f = createFake(); f.ready(); const response = f.submit(); f.context.ai2sOnSubmit(f.event());
  const id = f.member().file.id, before = plain(f.docs[id]);
  response.respondentEmail = 'other@example.invalid';
  assert.equal(f.context.ai2sOnSubmit(f.event('response-1', response.respondentEmail)).status, 'blocked');
  assert.equal(f.record('response:response-1').blocked, 'IDENTITY_MISMATCH');
  assert.deepEqual(f.docs[id], before);
  response.respondentEmail = 'member@example.invalid';
  assert.equal(f.context.ai2sRetry().blocked, 1);
  f.context.ai2sPause();
  f.properties.AI2S_RECOVERY = JSON.stringify({ action: 'resolveIdentity', responseId: 'response-1', originalEmail: 'other@example.invalid' });
  assert.equal(f.context.ai2sRecover().code, 'RECOVERY_MISMATCH');
  f.properties.AI2S_RECOVERY = JSON.stringify({ action: 'resolveIdentity', responseId: 'response-1', originalEmail: 'member@example.invalid' });
  assert.equal(f.context.ai2sRecover().status, 'recovered');
  f.context.ai2sInstallTriggers();
  assert.equal(f.context.ai2sRetry().blocked, 0);
  assert.equal(f.member().file.id, id);
});

test('unbound edited responses and multiple responses for one account do not create profiles', () => {
  const f = createFake(); f.ready(); const response = f.submit(); edit(f, response, 'role', 'Already edited');
  assert.equal(f.context.ai2sRetry().blocked, 1);
  assert.equal(f.record('response:response-1').blocked, 'UNBOUND_EDITED_RESPONSE');
  assert.equal(f.member(), null);
  const g = createFake(); g.ready(); g.submit(); g.context.ai2sRetry();
  g.submit('cv', 'response-2');
  assert.equal(g.context.ai2sOnSubmit(g.event('response-2')).status, 'blocked');
  assert.equal(g.record('response:response-2').blocked, 'DUPLICATE_MEMBER_RESPONSE');
  assert.equal(g.calls.filter((c) => c.url.includes('/copy')).length, 1);
});

test('permissions fail closed for inherited public access, wrong owner, raw sharing and member resharing', () => {
  const f = createFake();
  f.files['owner-root'].acl.push({ type: 'anyone', role: 'reader' });
  assert.equal(f.context.ai2sSetup().code, 'UNEXPECTED_ACCESS');
  assert.equal(Object.keys(f.forms).length, 0);
  const g = createFake(); g.ready(); g.submit();
  g.files[g.state().sheet.id].acl.push({ type: 'group', emailAddress: 'team@example.invalid', role: 'reader' });
  assert.equal(g.context.ai2sRetry().code, 'UNEXPECTED_ACCESS');
  assert.equal(g.member(), null);
  const h = createFake(); h.ready(); h.submit(); h.context.ai2sRetry();
  h.files[h.member().file.id].writersCanShare = true;
  assert.equal(h.context.ai2sRetry().pending, 1);
  assert.equal(h.member().error, 'SHARING_ENABLED');
  h.effectiveEmail = 'coordinator@example.invalid';
  assert.equal(h.context.ai2sRetry().code, 'OWNER_REQUIRED');
});

test('deployment validation checks actual Verified mode, publication, summaries and responder ACL', () => {
  const f = createFake(); f.context.ai2sSetup();
  assert.equal(f.context.ai2sValidate().code, 'FORM_NOT_PUBLISHED');
  f.publish();
  const form = f.forms[f.state().form.id];
  form.settings.emailCollectionType = 'RESPONDER_INPUT';
  assert.equal(f.context.ai2sValidate().code, 'EMAIL_NOT_VERIFIED');
  form.settings.emailCollectionType = 'VERIFIED';
  form.summary = true;
  assert.equal(f.context.ai2sValidate().code, 'FORM_SETTINGS');
  form.summary = false;
  f.files[form.formId].acl.push({ type: 'anyone', role: 'publishedReader', view: 'published' });
  assert.equal(f.context.ai2sValidate().code, 'RESPONDER_ACCESS');
});

test('literal Sheet writes and safe logs do not execute formulas or expose respondent/provider text', () => {
  const f = createFake(); f.ready(); const response = f.submit();
  const questionId = f.state().items.strength1.questionId;
  response.answers[questionId].textAnswers.answers[0].value = '=IMPORTXML("https://example.invalid/private", "//a")';
  f.faults.push({ match: (u) => u.pathname.endsWith('/copy'), status: 429 });
  assert.equal(f.context.ai2sRetry().pending, 1);
  const raw = f.sheets[f.state().sheet.id].Responses[0];
  assert.ok(raw[1].includes('IMPORTXML'));
  assert.doesNotMatch(f.logs.join('\n'), /IMPORTXML|PRIVATE|member@example/);
  for (const call of f.calls.filter((c) => c.method === 'put')) {
    assert.equal(new URL(call.url).searchParams.get('valueInputOption'), 'RAW');
  }
});

test('pause survives config drift, removes only processing triggers and preserves all data', () => {
  const f = createFake(); f.ready(); f.submit(); f.context.ai2sRetry();
  const before = plain({ files: f.files, docs: f.docs, forms: f.forms, sheets: f.sheets });
  f.triggers.push({ getHandlerFunction: () => 'unrelatedHandler' });
  f.properties.AI2S_CONFIG = JSON.stringify({ ...config, privateRootId: 'different-root' });
  assert.equal(f.context.ai2sRetry().code, 'CONFIG_CHANGED');
  assert.equal(f.context.ai2sPause().status, 'paused');
  assert.equal(f.state().paused, true);
  assert.equal(f.triggers.length, 1);
  assert.deepEqual({ files: f.files, docs: f.docs, forms: f.forms, sheets: f.sheets }, before);
});

test('trigger creation recovers an uncertain return and remains paused until both are installed', () => {
  const f = createFake(); f.context.ai2sSetup(); f.publish(); f.loseTrigger = true;
  assert.equal(f.context.ai2sInstallTriggers().status, 'blocked');
  assert.equal(f.state().paused, true);
  assert.equal(f.triggers.length, 1);
  assert.equal(f.context.ai2sInstallTriggers().status, 'enabled');
  assert.equal(f.triggers.length, 2);
  assert.equal(f.context.ai2sInstallTriggers().code, 'PAUSE_REQUIRED');
  f.context.ai2sPause();
  assert.equal(f.context.ai2sRetry().status, 'paused');
});

test('tab removal, moved profiles and missing assets stop instead of recreating or writing another tab', () => {
  const f = createFake(); f.ready(); const response = f.submit(); f.context.ai2sRetry();
  const id = f.member().file.id;
  f.docs[id].tabs[0].tabProperties.title = 'Member notes';
  edit(f, response, 'role', 'Changed role');
  assert.equal(f.context.ai2sRetry().pending, 1);
  assert.equal(f.member().error, 'TABS_CHANGED');
  f.files[id].parents = ['owner-root'];
  assert.equal(f.context.ai2sRetry().pending, 1);
  assert.equal(f.member().error, 'UNEXPECTED_PARENT');
  delete f.files[id];
  assert.equal(f.context.ai2sRetry().pending, 1);
  assert.equal(f.calls.filter((c) => c.url.includes('/copy')).length, 1);
});
