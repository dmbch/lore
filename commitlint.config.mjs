export default {
  extends: ['@commitlint/config-conventional'],
  rules: {
    'scope-empty': [2, 'always'],
    'type-enum': [2, 'always',
      ['feat', 'fix', 'docs', 'style', 'refactor', 'test', 'chore', 'ci'],
    ],
  },
};
