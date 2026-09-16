/* Google API boundary. No provider response/error text is logged. */
var Ai2sGoogle = (function () {
  'use strict';
  var endpoints = {
    drive: 'https://www.googleapis.com/drive/v3/',
    docs: 'https://docs.googleapis.com/v1/',
    sheets: 'https://sheets.googleapis.com/v4/',
    forms: 'https://forms.googleapis.com/v1/'
  };
  var mime = {
    form: 'application/vnd.google-apps.form', sheet: 'application/vnd.google-apps.spreadsheet',
    template: 'application/vnd.google-apps.document', profile: 'application/vnd.google-apps.document',
    folder: 'application/vnd.google-apps.folder'
  };
  function request(service, path, method, body, query) {
    if (!endpoints[service] || path.indexOf('://') !== -1) Ai2sSafety.fail('INVALID_ENDPOINT');
    var qs = Object.keys(query || {}).map(function (k) {
      return encodeURIComponent(k) + '=' + encodeURIComponent(query[k]);
    }).join('&');
    var options = { method: method || 'get', muteHttpExceptions: true, followRedirects: false,
      headers: { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() } };
    if (body !== undefined && body !== null) {
      options.contentType = 'application/json';
      options.payload = JSON.stringify(body);
    }
    var result;
    try { result = UrlFetchApp.fetch(endpoints[service] + path + (qs ? '?' + qs : ''), options); }
    catch (_) { Ai2sSafety.fail('REMOTE_FAILURE'); }
    var status = result.getResponseCode();
    if (status < 200 || status >= 300) {
      Ai2sSafety.fail(({ 400: 'REQUEST_REJECTED', 403: 'ACCESS_DENIED', 404: 'ASSET_UNAVAILABLE',
        409: 'REMOTE_CONFLICT', 429: 'QUOTA_RETRY' })[status] || 'REMOTE_RETRY');
    }
    try { return result.getContentText() ? JSON.parse(result.getContentText()) : {}; }
    catch (_) { Ai2sSafety.fail('REMOTE_FORMAT'); }
  }
  function all(service, path, field, query) {
    var result = [], token = '', seen = {};
    do {
      var q = Object.assign({}, query || {});
      if (token) q.pageToken = token;
      var page = request(service, path, 'get', null, q);
      result = result.concat(page[field] || []);
      token = page.nextPageToken || '';
      if (token && seen[token]) Ai2sSafety.fail('PAGINATION_FAILED');
      seen[token] = true;
    } while (token);
    return result;
  }
  function file(id) {
    return request('drive', 'files/' + Ai2sSafety.id(id), 'get', null, {
      fields: 'id,name,mimeType,parents,owners(emailAddress),driveId,trashed,appProperties,writersCanShare'
    });
  }
  function permissions(id, published) {
    var query = { pageSize: 100, fields: 'nextPageToken,permissions(id,type,emailAddress,role,view,deleted,pendingOwner,expirationTime)' };
    if (published) query.includePermissionsForView = 'published';
    return all('drive', 'files/' + Ai2sSafety.id(id) + '/permissions', 'permissions', query);
  }
  function owned(metadata, c, kind) {
    if (metadata.trashed || metadata.driveId || !metadata.owners || metadata.owners.length !== 1 ||
        Ai2sSafety.email(metadata.owners[0].emailAddress) !== c.ownerEmail ||
        (kind && metadata.mimeType !== mime[kind])) Ai2sSafety.fail('ASSET_OWNERSHIP');
  }
  function audit(id, c, allowed, requireAll, kind, allowPublished) {
    var metadata = file(id);
    owned(metadata, c, kind);
    // Include the published view for every asset so web publication cannot bypass ACL checks.
    var acl = permissions(id, true);
    Ai2sSafety.permissions(acl.filter(function (p) {
      return allowPublished ? p.view !== 'published' && p.role !== 'publishedReader' : true;
    }), allowed, requireAll);
    return metadata;
  }
  function root(c) {
    var id = c.privateRootId, seen = {};
    while (id) {
      if (seen[id]) Ai2sSafety.fail('FOLDER_CYCLE');
      seen[id] = true;
      var metadata = audit(id, c, Ai2sSafety.roles(c, false), false, 'folder');
      if ((metadata.parents || []).length > 1) Ai2sSafety.fail('UNEXPECTED_PARENT');
      id = (metadata.parents || [])[0];
    }
  }
  function grant(id, c, allowed, published) {
    var metadata = audit(id, c, allowed, false, null, published);
    var acl = permissions(id, !!published);
    Object.keys(allowed).forEach(function (key) {
      if (allowed[key] === 'owner') return;
      var separator = key.indexOf(':'), type = key.slice(0, separator), email = key.slice(separator + 1);
      if (!acl.some(function (p) { return p.type === type && p.emailAddress &&
          p.emailAddress.toLowerCase() === email && p.role === allowed[key] && !p.view; })) {
        request('drive', 'files/' + id + '/permissions', 'post', {
          type: type, emailAddress: email, role: allowed[key]
        }, { sendNotificationEmail: false, fields: 'id' });
      }
    });
    if (metadata.mimeType !== mime.folder && metadata.writersCanShare !== false) {
      request('drive', 'files/' + id, 'patch', { writersCanShare: false });
    }
    audit(id, c, allowed, true, null, published);
  }
  function parent(id, target, allowedOld) {
    var metadata = file(id), parents = metadata.parents || [];
    if (parents.length !== 1 || allowedOld.indexOf(parents[0]) < 0) Ai2sSafety.fail('UNEXPECTED_PARENT');
    if (parents[0] !== target) request('drive', 'files/' + id, 'patch', {}, {
      addParents: target, removeParents: parents[0], fields: 'id,parents'
    });
  }
  function marker(op) { return 'AI2S ' + op.kind + ' ' + Ai2sSafety.id(op.token); }
  function connection(c, state) {
    // Execution-local cache under the script lock, never a substitute for durable writes.
    var rowCache = {};
    var io = {
      token: function () { return Utilities.getUuid(); },
      now: function () { return new Date().toISOString(); },
      hash: function (text) {
        return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, text,
          Utilities.Charset.UTF_8).map(function (b) { return ('0' + ((b + 256) % 256).toString(16)).slice(-2); }).join('');
      },
      log: function (code) { console.log(JSON.stringify({ code: code })); },
      templateId: state.template && state.template.id,
      findCreated: function (op) {
        // Opaque marker survives uncertain native creation; profile copies get it atomically.
        var q = "trashed = false and 'me' in owners and mimeType = '" + mime[op.kind] + "' and ";
        q += op.kind === 'profile' ? "appProperties has { key='ai2sOperation' and value='" +
          Ai2sSafety.id(op.token) + "' }" : "name = '" + marker(op) + "'";
        return all('drive', 'files', 'files', { q: q, fields: 'nextPageToken,files(id)', pageSize: 100 })
          .map(function (f) { return f.id; });
      },
      create: function (op, sourceId) {
        if (op.kind === 'form') return FormApp.create(marker(op), false).getId();
        if (op.kind === 'sheet') return request('sheets', 'spreadsheets', 'post', {
          properties: { title: marker(op) }, sheets: [
            { properties: { sheetId: 0, title: 'Register', gridProperties: { columnCount: 3 } } },
            { properties: { sheetId: 1, title: 'Responses', gridProperties: { columnCount: 3 } } }
          ]
        }).spreadsheetId;
        if (op.kind === 'template') return request('docs', 'documents', 'post', { title: marker(op) }).documentId;
        if (op.kind === 'folder') return request('drive', 'files', 'post', {
          name: marker(op), mimeType: mime.folder, parents: [c.privateRootId]
        }, { fields: 'id' }).id;
        if (op.kind !== 'profile') Ai2sSafety.fail('JOURNAL_MISMATCH');
        audit(sourceId, c, Ai2sSafety.roles(c, false), true, 'template');
        return request('drive', 'files/' + Ai2sSafety.id(sourceId) + '/copy', 'post', {
          name: marker(op), parents: [c.privateRootId], writersCanShare: false,
          appProperties: { ai2sOperation: op.token }
        }, { fields: 'id' }).id;
      },
      readDocument: function (id) {
        return request('docs', 'documents/' + Ai2sSafety.id(id), 'get', null, { includeTabsContent: true });
      },
      writeDocument: function (id, body) {
        return request('docs', 'documents/' + Ai2sSafety.id(id) + ':batchUpdate', 'post', body);
      },
      nameProfile: function (id, name) {
        request('drive', 'files/' + Ai2sSafety.id(id), 'patch', { name: 'AI2S — ' + name.trim() });
      },
      latest: function (id) {
        var settings = request('forms', 'forms/' + state.form.id, 'get', null, { fields: 'settings' });
        if (!settings.settings || settings.settings.emailCollectionType !== 'VERIFIED') {
          Ai2sSafety.fail('EMAIL_NOT_VERIFIED');
        }
        var r = request('forms', 'forms/' + state.form.id + '/responses/' + Ai2sSafety.id(id));
        return { id: r.responseId, email: r.respondentEmail, createdAt: r.createTime,
          updatedAt: r.lastSubmittedTime, answers: Ai2sFormSchema.answers(r, state.items) };
      },
      responseIds: function () {
        return all('forms', 'forms/' + state.form.id + '/responses', 'responses', { pageSize: 500 })
          .map(function (r) { return r.responseId; }).sort();
      },
      auditProfile: function (id, member, complete) {
        var metadata = audit(id, c, Ai2sSafety.roles(c, true, member), false, 'profile');
        var location = (metadata.parents || [])[0];
        if (!metadata.parents || metadata.parents.length !== 1 ||
            [c.privateRootId, state.folder.id].indexOf(location) < 0) Ai2sSafety.fail('UNEXPECTED_PARENT');
        if (metadata.writersCanShare !== false) Ai2sSafety.fail('SHARING_ENABLED');
        if (complete) {
          if (location !== state.folder.id) Ai2sSafety.fail('UNEXPECTED_PARENT');
          audit(id, c, Ai2sSafety.roles(c, true, member), true, 'profile');
        }
      },
      shareProfile: function (id, member) {
        // Profile is already populated. Permissions are idempotent and notifications stay off.
        root(c);
        audit(state.folder.id, c, Ai2sSafety.roles(c, true), true, 'folder');
        grant(id, c, Ai2sSafety.roles(c, true, member));
        parent(id, state.folder.id, [c.privateRootId, state.folder.id]);
      }
    };
    function rows(tab) {
      if (rowCache[tab]) return rowCache[tab];
      audit(state.sheet.id, c, Ai2sSafety.roles(c, false), true, 'sheet');
      rowCache[tab] = request('sheets', 'spreadsheets/' + state.sheet.id + '/values/' +
        encodeURIComponent(tab + '!A:C'), 'get', null, { valueRenderOption: 'UNFORMATTED_VALUE' }).values || [];
      return rowCache[tab];
    }
    function locate(tab, key) {
      var values = rows(tab), matches = [];
      values.forEach(function (r, i) { if (r[0] === key) matches.push(i); });
      if (matches.length > 1) Ai2sSafety.fail('REGISTER_DUPLICATE');
      return { values: values, index: matches.length ? matches[0] : -1 };
    }
    function put(tab, key, value) {
      var found = locate(tab, key), row = found.index < 0 ? found.values.length + 1 : found.index + 1;
      var json = JSON.stringify(value);
      if (json.length > 45000) Ai2sSafety.fail('RECORD_TOO_LARGE');
      // Extend a small pilot sheet without deleting data when its grid fills.
      var sheets = request('sheets', 'spreadsheets/' + state.sheet.id, 'get', null,
        { fields: 'sheets(properties(sheetId,title,gridProperties(rowCount)))' }).sheets;
      var sheet = sheets.filter(function (s) { return s.properties.title === tab; })[0];
      if (!sheet) Ai2sSafety.fail('REGISTER_MISSING');
      if (row > sheet.properties.gridProperties.rowCount) request('sheets',
        'spreadsheets/' + state.sheet.id + ':batchUpdate', 'post', { requests: [{
          appendDimension: { sheetId: sheet.properties.sheetId, dimension: 'ROWS', length: 1000 }
        }] });
      audit(state.sheet.id, c, Ai2sSafety.roles(c, false), true, 'sheet');
      var cells = [key, json, io.now()];
      // Invalidate before a possibly uncertain write; the next attempt must read durable state.
      delete rowCache[tab];
      request('sheets', 'spreadsheets/' + state.sheet.id + '/values/' +
        encodeURIComponent(tab + '!A' + row + ':C' + row), 'put',
      { values: [cells] }, { valueInputOption: 'RAW' });
      found.values[row - 1] = cells;
      rowCache[tab] = found.values;
    }
    io.get = function (key) {
      var found = locate('Register', key);
      if (found.index < 0) return null;
      try {
        var value = JSON.parse(found.values[found.index][1]);
        if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error();
        return value;
      } catch (_) { Ai2sSafety.fail('REGISTER_CORRUPT'); }
    };
    io.put = function (key, value) { put('Register', key, value); };
    io.raw = function (response) { put('Responses', response.id, response); };
    return io;
  }
  return { request: request, all: all, file: file, permissions: permissions, owned: owned,
    audit: audit, root: root, grant: grant, parent: parent, connection: connection, mime: mime };
}());
