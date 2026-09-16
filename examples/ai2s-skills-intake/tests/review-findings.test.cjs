'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createFake, plain, fixtures } = require('./google-fake.cjs');

test('F1: a malformed response ID does not starve later responses on repeated reconciliation', () => {
  const f = createFake(); f.ready();
  f.submit('accountant', '!invalid-id', 'invalid@example.invalid');
  f.submit();
  for (let n = 0; n < 2; n++) {
    const result = f.context.ai2sReconcile();
    assert.equal(result.status, 'reconciled');
    assert.equal(result.processed, 2);
    assert.equal(result.pending, 1);
    assert.equal(f.member().status, 'complete');
  }
  assert.equal(f.calls.filter((c) => c.url.includes('/copy')).length, 1);
  assert.ok(f.logs.some((s) => s.includes('INVALID_ID')));
  assert.ok(f.logs.every((s) => !s.includes('!invalid-id')));
});

test('F1: a global cursor persistence failure stops the batch without losing profile identity', () => {
  const f = createFake(); f.ready();
  f.submit('accountant', 'response-a');
  f.submit('cv', 'response-b', 'second@example.invalid');
  const before = f.properties.AI2S_STATE;
  f.onSetProperty = (key, value) => {
    if (key === 'AI2S_STATE' && JSON.parse(value).cursor !== undefined) throw new Error('private storage error');
  };
  assert.equal(f.context.ai2sReconcile().status, 'blocked');
  assert.equal(f.properties.AI2S_STATE, before);
  assert.equal(f.calls.filter((c) => c.url.includes('/copy')).length, 1);
  const id = f.member().file.id;
  f.onSetProperty = null;
  assert.equal(f.context.ai2sReconcile().processed, 2);
  assert.equal(f.member().file.id, id);
  assert.equal(f.calls.filter((c) => c.url.includes('/copy')).length, 2);
});

test('F3: corrupt completed member records stay blocked through retries and never authorize another copy', () => {
  for (const remove of ['file', 'id']) {
    const f = createFake(); f.ready(); f.submit(); f.context.ai2sRetry();
    const docs = plain(f.docs), fileIds = Object.keys(f.files);
    const row = f.sheets[f.state().sheet.id].Register.find((r) => r[0] === 'member:member@example.invalid');
    const member = JSON.parse(row[1]);
    if (remove === 'file') delete member.file;
    else delete member.file.id;
    row[1] = JSON.stringify(member);
    const outcomes = Array.from({ length: 3 }, () => f.context.ai2sRetry());
    assert.deepEqual(Object.keys(f.files), fileIds, 'Corruption is not creation authorization');
    assert.equal(f.calls.filter((c) => c.url.includes('/copy')).length, 1);
    assert.deepEqual(f.docs, docs);
    assert.ok(outcomes.every((r) => r.blocked === 1));
    assert.equal(f.record('response:response-1').blocked, 'REGISTER_CORRUPT');
  }
});

test('F4: explicit link annotations survive label changes without linking arbitrary display text', () => {
  const f = createFake(); f.ready();
  const model = plain(f.context.Ai2sProfile.build({ ...fixtures[0].answers,
    evidenceLink: 'https://example.org/research' }, '2026-09-16T18:00:00Z'));
  const marked = model.paragraphs.find((p) => p.linkCandidate);
  assert.ok(marked, 'Only the respondent reference receives a structured candidate');
  assert.equal(model.paragraphs.filter((p) => p.linkCandidate).length, 1);
  const url = marked.linkCandidate.url;
  marked.text = '📚 Renamed reference: ' + url;
  marked.linkCandidate.offset = '📚 Renamed reference: '.length;
  model.paragraphs.push({ style: 'NORMAL_TEXT',
    text: 'Project, portfolio, or other link (unverified): https://unmarked.example.org/' });
  const state = f.state();
  const update = f.context.Ai2sDocuments.profileUpdate(f.docs[state.template.id], state.templateTabs, model);
  const links = update.requests.filter((r) => r.updateTextStyle?.textStyle?.link);
  assert.equal(links.length, 1);
  assert.equal(links[0].updateTextStyle.textStyle.link.url, url);
  const text = update.requests.find((r) => r.insertText).insertText.text;
  const range = links[0].updateTextStyle.range;
  assert.equal(text.slice(range.startIndex - 1, range.endIndex - 1), url);
});

test('F5: lock contention logs only the content-free busy result', () => {
  const f = createFake(); f.ready(); f.logs.length = 0; f.locked = true;
  assert.equal(f.context.ai2sReconcile().status, 'busy');
  assert.deepEqual(f.logs.map(JSON.parse), [{ status: 'busy' }]);
});

test('Q1: omitted empty descriptions already normalize without FORM_DRIFT', () => {
  const f = createFake(); f.ready();
  for (const item of f.forms[f.state().form.id].items) if (!item.description) delete item.description;
  assert.equal(f.context.ai2sValidate().status, 'validated');
});
