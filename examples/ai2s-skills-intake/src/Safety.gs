var Ai2sSafety = (function () {
  'use strict';
  function fail(code) {
    var error = new Error(code);
    error.ai2sCode = code;
    throw error;
  }
  function code(error) { return error && error.ai2sCode || 'REMOTE_FAILURE'; }
  function email(value) {
    if (typeof value !== 'string' || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim())) {
      fail('INVALID_EMAIL');
    }
    return value.trim().toLowerCase();
  }
  function id(value) {
    if (typeof value !== 'string' || !/^[a-zA-Z0-9_-]+$/.test(value)) fail('INVALID_ID');
    return value;
  }
  function canonical(value) {
    if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
    if (value && typeof value === 'object') {
      return '{' + Object.keys(value).sort().map(function (key) {
        return JSON.stringify(key) + ':' + canonical(value[key]);
      }).join(',') + '}';
    }
    return JSON.stringify(value);
  }
  function principals(value) {
    if (!Array.isArray(value) || !value.length) fail('CONFIG_REQUIRED');
    var seen = {};
    return value.map(function (p) {
      if (!p || ['user', 'group'].indexOf(p.type) < 0) fail('CONFIG_REQUIRED');
      var item = { type: p.type, email: email(p.email) };
      var key = item.type + ':' + item.email;
      if (seen[key]) fail('CONFIG_DUPLICATE');
      seen[key] = true;
      return item;
    });
  }
  function config(value) {
    if (!value || !Array.isArray(value.coordinatorEmails)) fail('CONFIG_REQUIRED');
    var result = {
      ownerEmail: email(value.ownerEmail),
      privateRootId: id(value.privateRootId),
      coordinatorEmails: value.coordinatorEmails.map(email).sort(),
      teamReaders: principals(value.teamReaders),
      responders: principals(value.responders)
    };
    if (new Set(result.coordinatorEmails).size !== result.coordinatorEmails.length ||
        result.coordinatorEmails.indexOf(result.ownerEmail) !== -1) fail('CONFIG_DUPLICATE');
    return result;
  }
  function roles(c, includeTeam, member) {
    var allowed = {};
    if (includeTeam) c.teamReaders.forEach(function (p) { allowed[p.type + ':' + p.email] = 'reader'; });
    if (member) allowed['user:' + email(member)] = 'writer';
    c.coordinatorEmails.forEach(function (e) { allowed['user:' + e] = 'writer'; });
    allowed['user:' + c.ownerEmail] = 'owner';
    return allowed;
  }
  function permissions(actual, allowed, requireAll) {
    var seen = {};
    actual.forEach(function (p) {
      // Publication ACLs are checked separately for Forms, never accepted as file access.
      if (p.view === 'published' || p.role === 'publishedReader') fail('UNEXPECTED_ACCESS');
      var key = p.type + ':' + (p.emailAddress || '').toLowerCase();
      if (p.deleted || p.pendingOwner || p.expirationTime || !allowed[key] ||
          allowed[key] !== p.role) fail('UNEXPECTED_ACCESS');
      seen[key] = true;
    });
    if (Object.keys(allowed).some(function (key) {
      return (requireAll || allowed[key] === 'owner') && !seen[key];
    })) {
      fail('MISSING_ACCESS');
    }
  }
  return { fail: fail, code: code, email: email, id: id, canonical: canonical,
    config: config, roles: roles, permissions: permissions };
}());
