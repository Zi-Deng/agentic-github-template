/* Pure field-to-paragraph mapping. No service access, scoring, or inference. */
var Ai2sProfile = (function () {
  'use strict';

  function text(value) {
    if (value === undefined || value === null) return '';
    if (typeof value !== 'string') throw new Error('INVALID_TEXT_ANSWER');
    return value.trim();
  }

  function choices(value) {
    if (value === undefined || value === null || value === '') return [];
    if (!Array.isArray(value)) throw new Error('INVALID_CHECKBOX_ANSWER');
    return value.map(text).filter(function (item) { return item !== ''; });
  }

  function isoDate(value) {
    // Require an explicit offset so results never depend on the host's local zone.
    if (typeof value !== 'string' || !/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value)) {
      throw new Error('INVALID_PROFILE_TIMESTAMP');
    }
    var date = new Date(value);
    if (!Number.isFinite(date.getTime())) throw new Error('INVALID_PROFILE_TIMESTAMP');
    return date.toISOString();
  }

  function build(answers, responseUpdatedAt) {
    if (!answers || typeof answers !== 'object' || Array.isArray(answers)) {
      throw new Error('INVALID_ANSWERS');
    }
    var a = answers;
    var paragraphs = [];
    function add(style, value) { paragraphs.push({ style: style, text: value }); }
    function field(label, value) { add('NORMAL_TEXT', label + ': ' + (text(value) || 'Not reported')); }
    function list(label, value) {
      var selected = choices(value);
      add('NORMAL_TEXT', label + ': ' + (selected.length ? selected.join('; ') : 'Not reported'));
    }
    function strength(number) {
      var key = 'strength' + number;
      if (number !== 1 && ![key, key + 'Experience', key + 'Recency', key + 'Example'].some(
        function (name) { return text(a[name]); }
      )) return;
      add('HEADING_2', text(a[key]) || 'Strength ' + number + ' — not named');
      field('Self-described experience', a[key + 'Experience']);
      field('Last used', a[key + 'Recency']);
      if (number === 1) field('Evidence context (self-reported)', a[key + 'Evidence']);
      field('Example (self-reported)', a[key + 'Example']);
    }

    add('TITLE', text(a.name) || 'Name not reported');
    add('SUBTITLE', 'AI2S member profile — self-reported');
    field('Role or main area of work', a.role);
    add('NORMAL_TEXT', 'Last response update: ' + isoDate(responseUpdatedAt) + ' (UTC)');
    add('NORMAL_TEXT', 'Experience, examples, and references are supplied by the member and are not ' +
      'independently verified. Missing answers mean not reported. No overall expertise score is assigned.');
    add('HEADING_1', 'Contribution overview');
    list('Could contribute to', a.contributions);
    add('HEADING_1', 'Named strengths');
    strength(1);
    // Edited forms can retain answers to sections subsequently skipped. Respect the current route.
    if (a.moreDetail === 'Add detail') {
      strength(2);
      strength(3);
    }
    add('HEADING_1', 'Respondent-supplied reference');
    field('Project, portfolio, or other link (unverified)', a.evidenceLink);
    if (a.moreDetail === 'Add detail' && a.technicalDetail === 'Add technical detail') {
      add('HEADING_1', 'Optional technical details');
      list('Reported areas', a.technicalAreas);
      list('Reported activities', a.technicalActivities);
      field('Reported tools', a.tools);
      field('Additional context', a.additionalContext);
    }
    add('HEADING_1', 'Learning interests');
    list('Would like to learn', a.learning);
    add('HEADING_1', 'Collaboration preferences');
    list('Preferred ways to collaborate', a.collaboration);
    add('NORMAL_TEXT', 'Preferences do not imply an availability commitment.');
    add('NORMAL_TEXT', 'This tab is generated from the intake. Edit your response to update it. ' +
      'Use Member notes for durable additions, qualifications, context, and corrections. ' +
      'Both tabs are readable by the team.');
    return { schemaVersion: 1, paragraphs: paragraphs };
  }

  return { build: build };
}());
