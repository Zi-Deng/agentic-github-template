var Ai2sRecovery = (function () {
  'use strict';
  // save MUST persist the journal before any external create/copy request is sent.
  function ensure(record, key, kind, io, save, sourceId) {
    if (!record[key]) {
      record[key] = { token: Ai2sSafety.id(io.token()), kind: kind, status: 'prepared' };
      save();
    }
    var operation = record[key];
    if (operation.kind !== kind) Ai2sSafety.fail('JOURNAL_MISMATCH');
    if (operation.id) return operation.id;
    if (operation.status === 'attempted') {
      var found = io.findCreated(operation);
      if (found.length > 1) Ai2sSafety.fail('CREATION_AMBIGUOUS');
      if (!found.length) Ai2sSafety.fail('CREATION_UNCERTAIN');
      operation.id = found[0];
    } else if (operation.status === 'prepared') {
      operation.status = 'attempted';
      save();
      operation.id = io.create(operation, sourceId);
    } else {
      Ai2sSafety.fail('JOURNAL_MISMATCH');
    }
    Ai2sSafety.id(operation.id);
    operation.status = 'created';
    save();
    return operation.id;
  }
  function confirmAbsent(operation, token, io, save) {
    if (!operation || operation.id || operation.status !== 'attempted' || token !== operation.token) {
      Ai2sSafety.fail('RECOVERY_MISMATCH');
    }
    if (io.findCreated(operation).length) Ai2sSafety.fail('CREATION_FOUND');
    operation.status = 'prepared';
    operation.confirmedAbsentAt = io.now();
    operation.confirmedAbsentRetries = (operation.confirmedAbsentRetries || 0) + 1;
    save();
  }
  return { ensure: ensure, confirmAbsent: confirmAbsent };
}());
