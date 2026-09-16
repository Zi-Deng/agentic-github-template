/* Pure questionnaire data shared by Apps Script and the Node.js tests. */
var Ai2sQuestionnaire = (function () {
  'use strict';

  var experience = [
    'Familiar with the concepts; have not applied them yet.',
    'Have applied them with guidance.',
    'Can apply them independently in familiar situations.',
    'Can adapt or design approaches for unfamiliar situations.',
    'Not sure how to describe my experience yet.'
  ];
  var recency = [
    'Within six months', 'Six–24 months ago', 'More than two years ago', 'Not applied yet'
  ];
  var evidence = [
    'Learning/coursework', 'Personal practice or prototype', 'Work/research project',
    'Ongoing operational work', 'Not yet applicable'
  ];

  function question(key, title, type, required, options, help) {
    return {
      key: key, title: title, type: type, required: required,
      options: options || [], help: help || ''
    };
  }

  function extraStrength(number) {
    var prefix = 'strength' + number;
    return [
      question(prefix, 'Name another strength (' + number + ')', 'text', false, [],
        'Any professional skill is welcome. You may leave this block blank.'),
      question(prefix + 'Experience', 'How have you used strength ' + number + '?',
        'choice', false, experience),
      question(prefix + 'Recency', 'When did you last use strength ' + number + '?',
        'choice', false, recency),
      question(prefix + 'Example', 'An example of strength ' + number, 'paragraph', false)
    ];
  }

  var core = [
    question('name', 'What name should appear on your profile?', 'text', true),
    question('role', 'What is your current role or main area of work?', 'text', true),
    question('strength1', 'What is one strength you would like teammates to know about?',
      'text', true, [], 'Any professional skill is welcome. “Still exploring” is also fine.'),
    question('strength1Experience', 'How have you used this strength?', 'choice', true, experience),
    question('strength1Recency', 'When did you last use it?', 'choice', true, recency),
    question('strength1Evidence', 'What experience best supports this strength?',
      'choice', false, evidence),
    question('contributions', 'Where could you contribute to a project?', 'checkbox', false, [
      'Subject or domain knowledge', 'Understanding stakeholder needs',
      'Data collection and analysis', 'AI/model research and development',
      'Software and data engineering', 'Deployment and operations',
      'Responsible AI, privacy, and security', 'Design and accessibility',
      'Communication and training', 'Project coordination',
      'Finance, grants, procurement, and administration'
    ], 'Choose any that you want to report; use Other for your own description.'),
    question('collaboration', 'How would you like to collaborate?', 'checkbox', false, [
      'Contributing', 'Learning alongside someone', 'Reviewing/advising', 'Mentoring'
    ], 'These are preferences, not an availability commitment.'),
    question('learning', 'What would you like to learn next?', 'checkbox', false, [
      'AI fundamentals', 'Data/statistics', 'Models', 'Software', 'Deployment',
      'Responsible AI', 'Project support'
    ], 'Use Other to name another topic.'),
    question('strength1Example', 'Can you share an example of your contribution?',
      'paragraph', false, [], 'A brief description is enough. Please omit confidential details.'),
    question('evidenceLink', 'Is there a relevant project, portfolio, or other link?',
      'text', false, [], 'Optional respondent-supplied reference; it will not be verified.'),
    question('moreDetail', 'Would you like to add more detail?', 'choice', true, [
      'Finish core', 'Add detail'
    ])
  ];
  core.filter(function (item) {
    return ['contributions', 'learning'].indexOf(item.key) !== -1;
  }).forEach(function (item) { item.other = true; });
  core[core.length - 1].routes = { 'Finish core': 'acknowledgement', 'Add detail': 'strengths' };

  var strengths = extraStrength(2).concat(extraStrength(3), [
    question('technicalDetail', 'Would you like to add optional technical detail?', 'choice', true,
      ['Skip technical detail', 'Add technical detail'])
  ]);
  strengths[strengths.length - 1].routes = {
    'Skip technical detail': 'acknowledgement', 'Add technical detail': 'technical'
  };
  var technical = [
    question('technicalAreas', 'Which technical areas would you like to report?', 'checkbox', false, [
      'Structured-data prediction', 'Language/LLMs', 'Images/video', 'Audio', 'Multimodal systems',
      'Forecasting', 'Search/recommendations', 'Graphs', 'Causal inference',
      'Reinforcement learning/robotics', 'Scientific ML'
    ]),
    question('technicalActivities', 'Which activities would you like to report?', 'checkbox', false, [
      'Data preparation', 'Experimentation', 'Training/fine-tuning',
      'Evaluation/calibration/robustness', 'Retrieval/RAG', 'Agent integration',
      'Deployment/monitoring', 'Distributed/HPC work', 'Privacy/security',
      'Responsible-AI evaluation', 'Reproducibility'
    ]),
    question('tools', 'Which languages, frameworks, platforms, or specialist tools do you use?',
      'text', false),
    question('additionalContext', 'What additional context would you like to share?', 'paragraph', false)
  ];
  technical[0].other = true;
  technical[1].other = true;

  return {
    version: 1,
    title: 'AI2S — Skills, Experience & Collaboration',
    introduction: 'Help teammates learn about your strengths and collaboration interests. ' +
      'The core takes about 5–7 minutes, with optional detail afterward. No AI experience is required. ' +
      'Your self-reported profile is readable by the AI2S team; raw responses are restricted to ' +
      'the owner and coordinators. You can edit your response later. ' +
      'Only report information you want on your team profile; omit confidential information. ' +
      'Unchecked options mean not reported, not a lack of skill.',
    experience: experience,
    recency: recency,
    evidence: evidence,
    sections: [
      { key: 'core', title: 'Your core profile', questions: core },
      { key: 'strengths', title: 'Optional strengths', questions: strengths },
      { key: 'technical', title: 'Optional technical detail', questions: technical,
        next: 'acknowledgement' },
      { key: 'acknowledgement', title: 'Profile visibility', next: 'submit', questions: [
        question('visibility', 'Please acknowledge your profile visibility', 'choice', true, [
          'I understand my profile is readable by the AI2S team.'
        ], 'You can edit your profile document. Put durable additions and corrections in Member notes; ' +
          'the Profile tab is regenerated from your response. Both tabs are visible to the team.')
      ] }
    ]
  };
}());
