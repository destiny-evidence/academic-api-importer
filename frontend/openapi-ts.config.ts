import { defineConfig } from '@hey-api/openapi-ts';

export default defineConfig({
  input: 'http://127.0.0.1:8000/openapi.json',
  output: 'src/plugins/api/spec',
  parser: {
    transforms: {
      enums: {
        case: 'PascalCase',
        enabled: true,
        mode: 'root',
      },
      // propertiesRequiredByDefault
      readWrite: {
        responses: {
          case: 'preserve',
        },
      },
    },
  },
  plugins: [
    {
      name: '@hey-api/client-fetch',
      exportFromIndex: true,
      bundle: true,
      baseUrl: false,
      throwOnError: true,
    },
    {
      name: '@hey-api/sdk',
      operations: { strategy: 'byTags' },
      paramsStructure: 'flat',
      responseStyle: 'fields',
      transformer: false,
      validator: {
        request: false,
        response: false,
      },
    },
    {
      name: '@hey-api/schemas',
      type: 'json',
    },
    {
      name: '@hey-api/typescript',
      case: 'PascalCase',
      enums: true,
    },
  ],
});
