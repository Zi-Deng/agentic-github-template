'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createFake, plain, config } = require('./google-fake.cjs');

test('configuration never defaults identities and checks duplicate or overly broad principals', () => {
  const f = createFake(), validate = f.context.Ai2sSafety.config;
  const example = JSON.parse(fs.readFileSync(path.join(__dirname, '../config.example.json'), 'utf8'));
  assert.throws(() => validate(example), /INVALID_EMAIL/);
  assert.throws(() => validate({ ...config, teamReaders: [] }), /CONFIG_REQUIRED/);
  assert.throws(() => validate({ ...config, responders: [{ type: 'domain', email: 'example.invalid' }] }), /CONFIG_REQUIRED/);
  assert.throws(() => validate({ ...config, coordinatorEmails: [config.ownerEmail] }), /CONFIG_DUPLICATE/);
  assert.equal(validate({ ...config, ownerEmail: ' OWNER@EXAMPLE.INVALID ' }).ownerEmail, config.ownerEmail);
});

test('item/answer IDs, required answers, Other values and schema drift are handled without title mapping', () => {
  const f = createFake(); f.ready();
  const r = f.submit('unlisted-specialty'), s = f.state(), api = f.context.Ai2sFormSchema;
  assert.equal(api.answers(r, s.items).contributions[0], 'Community stewardship of oral histories');
  delete r.answers[s.items.visibility.questionId];
  assert.throws(() => api.answers(r, s.items), /REQUIRED_ANSWER_MISSING/);
  r.answers.unknown = { questionId: 'unknown', textAnswers: { answers: [{ value: 'unexpected' }] } };
  assert.throws(() => api.answers(r, s.items), /ANSWER_DRIFT/);
  f.forms[s.form.id].items[0].title = 'Different title';
  assert.equal(f.context.ai2sValidate().code, 'FORM_DRIFT');
});

test('Docs requests preserve UTF-16 offsets and clear unsafe links without cross-tab replacement', () => {
  const f = createFake(); f.context.ai2sSetup();
  const s = f.state(), doc = f.docs[s.template.id], api = f.context.Ai2sDocuments;
  const paragraphs = [
    { style: 'TITLE', text: '🌱 Skills' },
    { style: 'NORMAL_TEXT', text: 'Project, portfolio, or other link (unverified): https://example.org/research' },
    { style: 'NORMAL_TEXT', text: 'Project, portfolio, or other link (unverified): javascript:alert(1)' }
  ];
  const update = plain(api.profileUpdate(doc, s.templateTabs, { paragraphs }));
  assert.equal(update.writeControl.requiredRevisionId, doc.revisionId);
  const styles = update.requests.filter((r) => r.updateParagraphStyle);
  assert.equal(styles[1].updateParagraphStyle.range.startIndex, '🌱 Skills\n'.length + 1);
  const links = update.requests.filter((r) => r.updateTextStyle?.textStyle?.link);
  assert.equal(links.length, 1);
  assert.equal(links[0].updateTextStyle.textStyle.link.url, 'https://example.org/research');
  assert.ok(update.requests.every((r) => !r.replaceAllText));
  assert.ok(update.requests.every((r) => {
    const op = Object.values(r)[0];
    return (op.range?.tabId || op.location?.tabId) === s.templateTabs.profile;
  }));
  assert.throws(() => api.profileUpdate(doc, s.templateTabs, {
    paragraphs: [{ style: 'TITLE', text: 'bad\u0000text' }]
  }), /UNSUPPORTED_TEXT/);
});

test('all API list operations follow pagination, including published access and creation recovery', () => {
  const f = createFake(); let count = 0;
  f.context.UrlFetchApp.fetch = (url) => {
    const query = new URL(url).searchParams;
    count++;
    if (count === 1) assert.equal(query.get('pageToken'), null);
    else assert.equal(query.get('pageToken'), 'page-two');
    return { getResponseCode: () => 200, getContentText: () => JSON.stringify(count === 1 ?
      { permissions: [{ id: 'one' }], nextPageToken: 'page-two' } : { permissions: [{ id: 'two' }] }) };
  };
  assert.deepEqual(plain(f.context.Ai2sGoogle.permissions('some-file', true)), [{ id: 'one' }, { id: 'two' }]);
  assert.equal(count, 2);
});

test('a failed journal persistence prevents external creation', () => {
  const f = createFake();
  f.onSetProperty = (key, value) => {
    if (key === 'AI2S_STATE' && JSON.parse(value).sheet?.status === 'attempted') throw new Error('lost state');
  };
  assert.equal(f.context.ai2sSetup().status, 'blocked');
  assert.equal(f.calls.filter((c) => c.url.endsWith('/spreadsheets') && c.method === 'post').length, 0);
});

test('corrupt deployment state and missing owner ACL cannot silently create a fresh deployment', () => {
  for (const state of ['[]', 'null', '{"sheet": {"id": "existing-sheet"}}']) {
    const f = createFake(); f.properties.AI2S_STATE = state;
    assert.equal(f.context.ai2sSetup().code, 'STATE_CORRUPT');
    assert.equal(Object.keys(f.sheets).length, 0);
  }
  const f = createFake();
  assert.throws(() => f.context.Ai2sSafety.permissions([], { 'user:owner@example.invalid': 'owner' }, false), /MISSING_ACCESS/);
});

test('missing triggers, raw auto-linking, wrong ownership and shared drives block validation', () => {
  const f = createFake(); f.ready();
  f.triggers = f.triggers.slice(1);
  assert.equal(f.context.ai2sValidate().code, 'TRIGGER_DRIFT');
  const g = createFake(); g.ready();
  g.forms[g.state().form.id].destination = 'auto-linked-sheet';
  assert.equal(g.context.ai2sValidate().code, 'AUTO_SHEET_LINKED');
  g.forms[g.state().form.id].destination = null;
  g.files[g.state().sheet.id].driveId = 'shared-drive';
  assert.equal(g.context.ai2sValidate().code, 'ASSET_OWNERSHIP');
  delete g.files[g.state().sheet.id].driveId;
  g.files[g.state().sheet.id].owners[0].emailAddress = 'someone-else@example.invalid';
  assert.equal(g.context.ai2sValidate().code, 'ASSET_OWNERSHIP');
});

test('published-to-web access on private assets or profiles is rejected', () => {
  const f = createFake(); f.ready(); f.submit(); f.context.ai2sRetry();
  f.files[f.member().file.id].acl.push({ type: 'anyone', role: 'publishedReader', view: 'published' });
  assert.equal(f.context.ai2sRetry().pending, 1);
  assert.equal(f.member().error, 'UNEXPECTED_ACCESS');
  f.files[f.state().sheet.id].acl.push({ type: 'anyone', role: 'publishedReader', view: 'published' });
  assert.equal(f.context.ai2sValidate().code, 'UNEXPECTED_ACCESS');
});

test('a missing document retains its recorded identity instead of creating an alternate profile', () => {
  const f = createFake(); f.ready(); f.submit(); f.context.ai2sRetry();
  const m = f.member(); delete f.docs[m.file.id];
  const r = f.forms[f.state().form.id].responses['response-1'];
  r.lastSubmittedTime = '2026-09-17T18:00:00.000Z';
  assert.equal(f.context.ai2sRetry().pending, 1);
  assert.equal(f.member().error, 'ASSET_UNAVAILABLE');
  assert.equal(f.member().file.id, m.file.id);
});

test('manifest confines fetches to Google endpoints and does not enable automatic exception logging', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '../appsscript.json'), 'utf8'));
  assert.equal(manifest.runtimeVersion, 'V8');
  assert.equal(manifest.exceptionLogging, 'NONE');
  assert.equal(manifest.urlFetchWhitelist.length, 4);
  assert.ok(manifest.urlFetchWhitelist.every((url) => /googleapis\.com\//.test(url)));
  assert.ok(!manifest.oauthScopes.some((scope) => /gmail|mail\.google|script\.send_mail/.test(scope)));
  assert.equal(manifest.webapp, undefined);
});
