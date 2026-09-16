'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createFake, plain, config } = require('./google-fake.cjs');

// Boundary expectations checked against official references on 2026-09-16:
// https://developers.google.com/workspace/forms/api/reference/rest/v1/forms/batchUpdate#UpdateItemRequest
// https://developers.google.com/workspace/forms/api/guides/publish-form
// https://developers.google.com/workspace/drive/api/guides/ref-roles#views

test('R1: real setup requests conform to the Forms UpdateItemRequest boundary', () => {
  const f = createFake();
  assert.equal(f.context.ai2sSetup().status, 'prepared');
  const updates = f.calls.filter((c) => c.url.startsWith('https://forms.googleapis.com/'))
    .flatMap((c) => c.body?.requests || []).filter((r) => r.updateItem).map((r) => r.updateItem);
  assert.ok(updates.length > 0);
  // Official schema: item, location and updateMask. Docs' fields key is not a Forms field mask.
  for (const update of updates) {
    assert.deepEqual(Object.keys(update).sort(), ['item', 'location', 'updateMask']);
    assert.equal(update.updateMask, '*');
    assert.ok(update.item.itemId);
  }
});

test('R2: documented published-view reader and publishedReader representations validate', () => {
  for (const permission of [
    { role: 'reader', view: 'published' },
    { role: 'publishedReader', view: 'published' },
    { role: 'publishedReader' }
  ]) {
    const f = createFake(); f.ready();
    const acl = f.files[f.state().form.id].acl;
    acl.splice(acl.findIndex((p) => p.id === 'responder-acl'), 1,
      { type: 'group', emailAddress: 'pilot@example.invalid', ...permission });
    assert.equal(f.context.ai2sValidate().status, 'validated', JSON.stringify(permission));
  }
});

test('R2: ordinary file readers and invalid published grants never become responder-only access', () => {
  for (const permission of [
    { role: 'reader' }, { role: 'reader', view: 'metadata' },
    { role: 'writer', view: 'published' }, { role: 'reader', view: 'published', pendingOwner: true },
    { role: 'reader', view: 'published', type: 'anyone' }
  ]) {
    const f = createFake(); f.ready();
    const acl = f.files[f.state().form.id].acl;
    acl.splice(acl.findIndex((p) => p.id === 'responder-acl'), 1,
      { type: 'group', emailAddress: 'pilot@example.invalid', ...permission });
    assert.equal(f.context.ai2sValidate().status, 'blocked');
  }
});

test('R3: the native template exposes every section, while populated profiles remove its guidance', () => {
  const f = createFake(); f.ready();
  const state = f.state(), text = f.docText(state.template.id, state.templateTabs.profile);
  for (const section of ['Role or main area of work', 'Last response update', 'Contribution overview',
    'Named strengths', 'Self-described experience', 'Last used', 'Evidence context',
    'Example', 'Respondent-supplied reference', 'Optional technical details',
    'Reported areas', 'Reported activities', 'Reported tools', 'Additional context',
    'Learning interests', 'Collaboration preferences']) assert.ok(text.includes(section), section);
  assert.match(text, /Template guidance:/);
  const templateNotes = plain(f.docs[state.template.id].tabs.find((t) => t.tabProperties.tabId === state.templateTabs.notes));
  f.submit();
  assert.equal(f.context.ai2sRetry().pending, 0);
  const member = f.member(), output = f.docText(member.file.id, member.tabs.profile);
  assert.doesNotMatch(output, /Template guidance:|\[From intake\]/);
  const notes = plain(f.docs[member.file.id].tabs.find((t) => t.tabProperties.tabId === member.tabs.notes));
  assert.deepEqual(notes, templateNotes);
});

function rollout(f) {
  const initial = JSON.parse(f.properties.AI2S_CONFIG);
  const request = { teamReaders: [...initial.teamReaders, { type: 'user', email: 'reader2@example.invalid' }],
    responders: [...initial.responders, { type: 'user', email: 'responder2@example.invalid' }] };
  f.properties.AI2S_ACCESS_UPDATE = JSON.stringify(request);
  // The owner supplies the actual grants manually, with invitations off; adoption never grants access.
  f.files[f.state().folder.id].acl.push({ type: 'user', emailAddress: 'reader2@example.invalid', role: 'reader' });
  f.files[f.state().form.id].acl.push({ type: 'user', emailAddress: 'responder2@example.invalid', role: 'reader', view: 'published' });
  return request;
}

test('R4: paused rollout adopts explicit user access and retains asset IDs, bindings and notes', () => {
  const f = createFake(); f.ready(); f.submit(); f.context.ai2sRetry();
  f.context.ai2sPause();
  const before = f.state(), member = plain(f.member()), binding = plain(f.record('response:response-1'));
  const docs = plain(f.docs), sheets = plain(f.sheets), request = rollout(f), count = f.calls.length;
  assert.equal(f.context.ai2sUpdateAccess().status, 'access-updated');
  assert.equal(f.state().paused, true);
  for (const kind of ['form', 'sheet', 'template', 'folder']) assert.deepEqual(f.state()[kind], before[kind]);
  assert.deepEqual(f.state().access, request);
  assert.equal(f.state().configDigest, before.configDigest);
  assert.deepEqual(f.member(), member);
  assert.deepEqual(f.record('response:response-1'), binding);
  assert.deepEqual(f.docs, docs);
  assert.deepEqual(f.sheets, sheets);
  assert.ok(f.calls.slice(count).every((c) => c.method === 'get'));
  assert.equal(f.context.ai2sInstallTriggers().status, 'enabled');
  assert.equal(f.context.ai2sOnSubmit(f.event()).status, 'unchanged');
});

test('R4: a pilot configured entirely with individual user lists can expand without a new deployment', () => {
  const f = createFake(), initial = plain(config);
  initial.teamReaders = [{ type: 'user', email: 'pilot-reader@example.invalid' }];
  initial.responders = [{ type: 'user', email: 'member@example.invalid' }];
  f.properties.AI2S_CONFIG = JSON.stringify(initial);
  assert.equal(f.context.ai2sSetup().status, 'prepared');
  f.publish();
  Object.assign(f.files[f.state().form.id].acl.find((p) => p.id === 'responder-acl'),
    { type: 'user', emailAddress: 'member@example.invalid', role: 'reader', view: 'published' });
  assert.equal(f.context.ai2sInstallTriggers().status, 'enabled');
  f.submit(); f.context.ai2sRetry(); f.context.ai2sPause();
  const id = f.member().file.id;
  rollout(f);
  assert.equal(f.context.ai2sUpdateAccess().status, 'access-updated');
  assert.equal(f.context.ai2sInstallTriggers().status, 'enabled');
  assert.equal(f.context.ai2sRetry().pending, 0);
  assert.equal(f.member().file.id, id);
  assert.ok(f.acl(id).some((p) => p.emailAddress === 'reader2@example.invalid' && p.role === 'reader'));
});

test('R4: active execution, missing grants, access removal and privileged config changes are refused', () => {
  const f = createFake(); f.ready();
  f.properties.AI2S_ACCESS_UPDATE = JSON.stringify({ teamReaders: config.teamReaders, responders: config.responders });
  assert.equal(f.context.ai2sUpdateAccess().code, 'PAUSE_REQUIRED');
  f.context.ai2sPause();
  const state = f.properties.AI2S_STATE;
  f.properties.AI2S_ACCESS_UPDATE = JSON.stringify({ teamReaders: [], responders: config.responders });
  assert.equal(f.context.ai2sUpdateAccess().status, 'blocked');
  const proposed = { teamReaders: [...config.teamReaders, { type: 'user', email: 'new@example.invalid' }],
    responders: config.responders };
  f.properties.AI2S_ACCESS_UPDATE = JSON.stringify(proposed);
  assert.equal(f.context.ai2sUpdateAccess().code, 'MISSING_ACCESS');
  for (const field of ['ownerEmail', 'privateRootId', 'coordinatorEmails']) {
    f.properties.AI2S_ACCESS_UPDATE = JSON.stringify({ ...proposed, [field]: config[field] });
    assert.equal(f.context.ai2sUpdateAccess().code, 'ACCESS_UPDATE_REQUIRED');
  }
  f.properties.AI2S_ACCESS_UPDATE = JSON.stringify({ ...proposed,
    responders: [{ type: 'user', email: 'replacement@example.invalid' }] });
  assert.equal(f.context.ai2sUpdateAccess().code, 'ACCESS_REMOVAL_UNSUPPORTED');
  assert.equal(f.properties.AI2S_STATE, state);
  f.effectiveEmail = 'coordinator@example.invalid';
  assert.equal(f.context.ai2sUpdateAccess().code, 'OWNER_REQUIRED');
});

test('R4: unsafe existing profile or raw-data access blocks adoption without changing the seal', () => {
  for (const kind of ['profile', 'sheet']) {
    const f = createFake(); f.ready(); f.submit(); f.context.ai2sRetry(); f.context.ai2sPause();
    rollout(f);
    const before = f.properties.AI2S_STATE;
    const id = kind === 'profile' ? f.member().file.id : f.state().sheet.id;
    f.files[id].acl.push({ type: 'anyone', role: 'reader' });
    assert.equal(f.context.ai2sUpdateAccess().code, 'UNEXPECTED_ACCESS');
    assert.equal(f.properties.AI2S_STATE, before);
  }
});

test('R4: interrupted access-state saves can retry without replacing deployment history', () => {
  for (const accepted of [false, true]) {
    const f = createFake(); f.ready(); f.submit(); f.context.ai2sRetry(); f.context.ai2sPause();
    const before = f.state(), request = rollout(f), member = plain(f.member());
    let failed = false;
    f.onSetProperty = (key, value) => {
      if (key === 'AI2S_STATE' && JSON.parse(value).access && !failed) {
        failed = true;
        if (accepted) f.properties[key] = value;
        throw new Error('uncertain property save');
      }
    };
    assert.equal(f.context.ai2sUpdateAccess().status, 'blocked');
    assert.equal(f.state().paused, true);
    assert.equal(f.context.ai2sUpdateAccess().status, 'access-updated');
    assert.deepEqual(f.state().access, request);
    assert.equal(f.state().configDigest, before.configDigest);
    assert.deepEqual(f.member(), member);
    assert.equal(f.context.ai2sInstallTriggers().status, 'enabled');
    assert.equal(f.context.ai2sRetry().pending, 0);
  }
});
