var Ai2sFormSchema = (function () {
  'use strict';
  function compile() {
    var items = [], mapping = {}, pages = {};
    Ai2sQuestionnaire.sections.forEach(function (section, index) {
      if (index) pages[section.key] = (0x1000 + index).toString(16);
    });
    Ai2sQuestionnaire.sections.forEach(function (section, index) {
      if (index) items.push({ itemId: pages[section.key], title: section.title, pageBreakItem: {} });
      section.questions.forEach(function (q) {
        var number = Object.keys(mapping).length;
        var itemId = (0x2000 + number).toString(16);
        var questionId = (0x3000 + number).toString(16);
        mapping[q.key] = { itemId: itemId, questionId: questionId };
        var question = { questionId: questionId, required: q.required };
        if (q.type === 'text' || q.type === 'paragraph') {
          question.textQuestion = { paragraph: q.type === 'paragraph' };
        } else {
          question.choiceQuestion = {
            type: q.type === 'checkbox' ? 'CHECKBOX' : 'RADIO', shuffle: false,
            options: q.options.map(function (value) {
              var option = { value: value };
              if (q.routes) option.goToSectionId = pages[q.routes[value]];
              return option;
            })
          };
          if (q.other) question.choiceQuestion.options.push({ value: 'Other', isOther: true });
        }
        items.push({ itemId: itemId, title: q.title, description: q.help,
          questionItem: { question: question } });
      });
    });
    return { items: items, mapping: mapping };
  }
  function comparable(item) {
    var value = { itemId: item.itemId, title: item.title || '', description: item.description || '' };
    if (item.pageBreakItem) value.pageBreakItem = {};
    else {
      var q = item.questionItem && item.questionItem.question;
      if (!q) Ai2sSafety.fail('FORM_DRIFT');
      var result = { questionId: q.questionId, required: !!q.required };
      if (q.textQuestion) result.textQuestion = { paragraph: !!q.textQuestion.paragraph };
      else if (q.choiceQuestion) {
        result.choiceQuestion = { type: q.choiceQuestion.type, shuffle: !!q.choiceQuestion.shuffle,
          options: q.choiceQuestion.options.map(function (o) {
            if (o.isOther) return { isOther: true };
            var option = { value: o.value };
            if (o.goToSectionId) option.goToSectionId = o.goToSectionId;
            if (o.goToAction) option.goToAction = o.goToAction;
            return option;
          }) };
      } else Ai2sSafety.fail('FORM_DRIFT');
      value.questionItem = { question: result };
    }
    return value;
  }
  function validate(form, mapping) {
    var expected = compile();
    if (Ai2sSafety.canonical(expected.mapping) !== Ai2sSafety.canonical(mapping) ||
        Ai2sSafety.canonical(expected.items.map(comparable)) !==
        Ai2sSafety.canonical((form.items || []).map(comparable))) Ai2sSafety.fail('FORM_DRIFT');
  }
  function answers(response, mapping) {
    var byId = {}, result = {};
    Ai2sQuestionnaire.sections.forEach(function (section) {
      section.questions.forEach(function (q) { byId[mapping[q.key].questionId] = q; });
    });
    Object.keys(response.answers || {}).forEach(function (key) {
      var q = byId[key], answer = response.answers[key];
      if (!q || answer.questionId !== key || !answer.textAnswers) Ai2sSafety.fail('ANSWER_DRIFT');
      var values = (answer.textAnswers.answers || []).map(function (a) {
        if (typeof a.value !== 'string') Ai2sSafety.fail('INVALID_ANSWER');
        return a.value;
      });
      if (q.type === 'checkbox') result[q.key] = values;
      else {
        if (values.length > 1) Ai2sSafety.fail('INVALID_ANSWER');
        result[q.key] = values[0] || '';
      }
      if (q.options.length && !q.other && values.some(function (v) {
        return v && q.options.indexOf(v) < 0;
      })) Ai2sSafety.fail('ANSWER_DRIFT');
    });
    Ai2sQuestionnaire.sections.forEach(function (section) {
      var active = section.key === 'core' || section.key === 'acknowledgement' ||
        (result.moreDetail === 'Add detail' && (section.key === 'strengths' ||
          result.technicalDetail === 'Add technical detail'));
      if (active) section.questions.forEach(function (q) {
        if (q.required && (typeof result[q.key] !== 'string' || !result[q.key].trim())) {
          Ai2sSafety.fail('REQUIRED_ANSWER_MISSING');
        }
      });
    });
    return result;
  }
  return { compile: compile, validate: validate, answers: answers, comparable: comparable };
}());
