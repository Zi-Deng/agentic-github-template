var Ai2sPipeline = (function () {
  'use strict';
  function process(io, responseId, eventEmail) {
    var key, binding, member, memberKey;
    function putBinding() { io.put(key, binding); }
    function block(reason) {
      binding.blocked = reason;
      binding.status = 'blocked';
      putBinding();
      Ai2sSafety.fail(reason);
    }
    function current() {
      var response = io.latest(responseId);
      if (response.id !== responseId) Ai2sSafety.fail('RESPONSE_MISMATCH');
      response.email = Ai2sSafety.email(response.email);
      if (binding && binding.email && response.email !== binding.email) block('IDENTITY_MISMATCH');
      return response;
    }
    function fingerprint(response) {
      return io.hash(Ai2sSafety.canonical({ version: 1, email: response.email,
        answers: response.answers, updatedAt: response.updatedAt }));
    }
    try {
      Ai2sSafety.id(responseId);
      key = 'response:' + responseId;
      binding = io.get(key);
      if (binding && binding.blocked) Ai2sSafety.fail(binding.blocked);
      var response = current();
      if (!binding) {
        binding = { email: null, status: 'pending' };
        // An edited response first seen after submission cannot prove its original account.
        // The owner must investigate it before binding; do not adopt a changed identity.
        if (!response.createdAt || response.createdAt !== response.updatedAt) {
          block('UNBOUND_EDITED_RESPONSE');
        }
        binding.email = eventEmail ? Ai2sSafety.email(eventEmail) : response.email;
        putBinding();
      }
      if (binding.email !== response.email || (eventEmail &&
          binding.email !== Ai2sSafety.email(eventEmail))) block('IDENTITY_MISMATCH');
      memberKey = 'member:' + binding.email;
      member = io.get(memberKey);
      if (member && member.responseId !== responseId) block('DUPLICATE_MEMBER_RESPONSE');
      if (member && (member.status === 'complete' || member.fingerprint || member.tabs || member.updatedAt) &&
          (!member.file || !member.file.id)) {
        // Persist a sticky block before the error handler changes status. Missing history
        // must never become authorization for a new copy on the next retry.
        block('REGISTER_CORRUPT');
      }
      if (!member) {
        member = { responseId: responseId, status: 'pending' };
        io.put(memberKey, member);
      }
      function saveMember() { io.put(memberKey, member); }
      var wanted = fingerprint(response);
      if (member.fingerprint === wanted && member.status === 'complete') {
        io.auditProfile(member.file.id, binding.email, true);
        if (binding.status !== 'complete' || binding.error) {
          binding.status = 'complete';
          delete binding.error;
          putBinding();
        }
        return { status: 'unchanged' };
      }
      // The restricted sheet uses literal RAW values, not the Forms auto-linked sheet.
      io.raw(response);
      var fileId = Ai2sRecovery.ensure(member, 'file', 'profile', io, saveMember, io.templateId);
      io.auditProfile(fileId, binding.email, false);
      var doc = io.readDocument(fileId);
      if (!member.tabs) {
        member.tabs = Ai2sDocuments.identify(doc);
        saveMember();
      }
      // Re-read after slow creation/read calls, ignoring the event's possibly stale answers.
      var latest = current();
      if (fingerprint(latest) !== wanted) Ai2sSafety.fail('RESPONSE_CHANGED');
      var model = Ai2sProfile.build(response.answers, response.updatedAt);
      var request = Ai2sDocuments.profileUpdate(doc, member.tabs, model);
      member.status = 'writing';
      saveMember();
      io.auditProfile(fileId, binding.email, false);
      io.writeDocument(fileId, request);
      // A crash after the write retries only the Profile tab against a new revision.
      member.status = 'populated';
      saveMember();
      io.nameProfile(fileId, response.answers.name);
      if (fingerprint(current()) !== wanted) Ai2sSafety.fail('RESPONSE_CHANGED');
      io.shareProfile(fileId, binding.email);
      io.auditProfile(fileId, binding.email, true);
      member.fingerprint = wanted;
      member.status = 'complete';
      member.updatedAt = response.updatedAt;
      delete member.error;
      saveMember();
      binding.status = 'complete';
      delete binding.error;
      putBinding();
      return { status: 'complete' };
    } catch (error) {
      var code = Ai2sSafety.code(error);
      // Persistence itself can fail. Never conceal that with a success result or raw provider log.
      try {
        if (binding) {
          binding.status = binding.blocked ? 'blocked' : 'pending';
          binding.error = code;
          putBinding();
        }
        if (member) {
          member.status = 'pending';
          member.error = code;
          io.put(memberKey, member);
        }
      } catch (_) { code = 'STATE_WRITE_FAILED'; }
      io.log(code);
      return { status: binding && binding.blocked ? 'blocked' : 'pending', code: code };
    }
  }
  function resolveIdentity(io, responseId, originalEmail) {
    var key = 'response:' + Ai2sSafety.id(responseId), binding = io.get(key);
    var original = Ai2sSafety.email(originalEmail);
    if (!binding || ['IDENTITY_MISMATCH', 'UNBOUND_EDITED_RESPONSE'].indexOf(binding.blocked) < 0 ||
        (binding.email && binding.email !== original)) Ai2sSafety.fail('RECOVERY_MISMATCH');
    var current = io.latest(responseId);
    if (Ai2sSafety.email(current.email) !== original) Ai2sSafety.fail('IDENTITY_MISMATCH');
    binding.email = original;
    binding.resolvedAt = io.now();
    binding.status = 'pending';
    delete binding.blocked;
    delete binding.error;
    io.put(key, binding);
  }
  return { process: process, resolveIdentity: resolveIdentity };
}());
