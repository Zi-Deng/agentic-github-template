var Ai2sDocuments = (function () {
  'use strict';
  function tabs(doc) {
    var result = [];
    function visit(tab) {
      result.push(tab);
      (tab.childTabs || []).forEach(visit);
    }
    (doc.tabs || []).forEach(visit);
    return result;
  }
  function identify(doc) {
    var all = tabs(doc);
    var profile = all.filter(function (t) { return t.tabProperties.title === 'Profile'; });
    var notes = all.filter(function (t) { return t.tabProperties.title === 'Member notes'; });
    if (all.length !== 2 || profile.length !== 1 || notes.length !== 1) Ai2sSafety.fail('TABS_CHANGED');
    return { profile: profile[0].tabProperties.tabId, notes: notes[0].tabProperties.tabId };
  }
  function select(doc, stored) {
    var found = identify(doc);
    if (!stored || stored.profile !== found.profile || stored.notes !== found.notes ||
        stored.profile === stored.notes) Ai2sSafety.fail('TABS_CHANGED');
    return tabs(doc).filter(function (t) { return t.tabProperties.tabId === stored.profile; })[0];
  }
  function update(doc, tabId, paragraphs) {
    if (!doc.revisionId) Ai2sSafety.fail('REVISION_MISSING');
    var tab = tabs(doc).filter(function (t) { return t.tabProperties.tabId === tabId; })[0];
    if (!tab || !tab.documentTab || !tab.documentTab.body) Ai2sSafety.fail('TABS_CHANGED');
    var body = tab.documentTab.body.content;
    var end = body[body.length - 1].endIndex - 1;
    if (!Number.isInteger(end) || end < 1) Ai2sSafety.fail('DOCUMENT_STRUCTURE');
    var text = '', ranges = [];
    paragraphs.forEach(function (p) {
      if (typeof p.text !== 'string' || /[\u0000-\u0008\u000b-\u001f\uE000-\uF8FF]/.test(p.text)) {
        Ai2sSafety.fail('UNSUPPORTED_TEXT');
      }
      var start = text.length + 1;
      text += p.text + '\n';
      ranges.push({ range: { tabId: tabId, startIndex: start, endIndex: text.length + 1 },
        paragraph: p });
    });
    if (!text) Ai2sSafety.fail('EMPTY_PROFILE');
    var requests = [];
    if (end > 1) requests.push({ deleteContentRange: {
      range: { tabId: tabId, startIndex: 1, endIndex: end }
    } });
    requests.push({ insertText: { location: { tabId: tabId, index: 1 }, text: text } });
    ranges.forEach(function (r) {
      var style = r.paragraph.style;
      var size = { TITLE: 20, SUBTITLE: 12, HEADING_1: 15, HEADING_2: 12, NORMAL_TEXT: 11 }[style];
      if (!size) Ai2sSafety.fail('INVALID_STYLE');
      requests.push({ updateParagraphStyle: { range: r.range,
        paragraphStyle: { namedStyleType: style, lineSpacing: 115,
          spaceAbove: { magnitude: style === 'NORMAL_TEXT' ? 0 : 8, unit: 'PT' },
          spaceBelow: { magnitude: 5, unit: 'PT' } },
        fields: 'namedStyleType,lineSpacing,spaceAbove,spaceBelow' } });
      requests.push({ updateTextStyle: { range: r.range, textStyle: {
        weightedFontFamily: { fontFamily: 'Arial' }, fontSize: { magnitude: size, unit: 'PT' },
        bold: style === 'HEADING_1' || style === 'HEADING_2',
        foregroundColor: { color: { rgbColor: style === 'NORMAL_TEXT' ?
          { red: 0.1, green: 0.1, blue: 0.1 } : { red: 0.1, green: 0.2, blue: 0.35 } } }
      }, fields: 'weightedFontFamily,fontSize,bold,foregroundColor,link' } });
      var candidate = r.paragraph.linkCandidate;
      if (candidate && typeof candidate.url === 'string' && Number.isInteger(candidate.offset) && candidate.offset >= 0) {
        var url = candidate.url;
        if (r.paragraph.text.slice(candidate.offset, candidate.offset + url.length) === url &&
            /^https?:\/\/[a-zA-Z0-9.-]+(?::\d+)?(?:[/?#][^\s<>"\\]*)?$/.test(url)) {
          requests.push({ updateTextStyle: { range: { tabId: tabId,
            startIndex: r.range.startIndex + candidate.offset,
            endIndex: r.range.startIndex + candidate.offset + url.length },
            textStyle: { link: { url: url } }, fields: 'link' } });
        }
      }
    });
    return { writeControl: { requiredRevisionId: doc.revisionId }, requests: requests };
  }
  function profileUpdate(doc, stored, model) {
    select(doc, stored);
    return update(doc, stored.profile, model.paragraphs);
  }
  return { tabs: tabs, identify: identify, select: select, update: update, profileUpdate: profileUpdate };
}());
