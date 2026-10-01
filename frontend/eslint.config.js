import js from '@eslint/js';
import globals from 'globals';
import pluginVue from 'eslint-plugin-vue';
import skipFormatting from '@vue/eslint-config-prettier/skip-formatting';
import { defineConfigWithVueTs, vueTsConfigs } from '@vue/eslint-config-typescript';

export default defineConfigWithVueTs(
  {
    name: 'app/files-to-lint',
    files: ['**/*.{ts,mts,vue}'],
  },
  {
    name: 'app/files-to-ignore',
    ignores: ['**/dist/**', '**/coverage/**', '**/src/plugins/api/spec/**'],
  },
  js.configs.recommended,
  pluginVue.configs['flat/recommended'],
  // Vue + TypeScript-aware parsing and rules (must come after the Vue configs).
  vueTsConfigs.recommended,
  {
    name: 'app/language-options',
    files: ['**/*.{ts,vue}'],
    languageOptions: {
      globals: {
        ...globals.browser,
      },
    },
  },
  {
    name: 'app/vite-config',
    files: ['vite.config.ts'],
    languageOptions: {
      globals: {
        ...globals.node,
      },
    },
  },

  skipFormatting,

  {
    rules: {
      'no-console': 'off',
      'no-debugger': 'off',

      'vue/max-len': [
        'error',
        {
          code: 140,
          ignorePattern: '(d|style)="[^"]*"',
          ignoreStrings: true,
          ignoreTrailingComments: true,
        },
      ],
      'vue/no-v-html': 'off',
      'object-curly-newline': 'off',
      'class-methods-use-this': 'off',
      'prefer-promise-reject-errors': 'off',
      'vue/no-mutating-props': 'off',
      '@typescript-eslint/no-explicit-any': 'off',

      'operator-linebreak': [
        'error',
        'after',
        {
          overrides: {
            '?': 'before',
            ':': 'before',
          },
        },
      ],
    },
  },
);
