# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

## 3D Scene Agent Web UI

This app is the Web chat interface for the 3D Scene Agent. It supports:

- Conversation history (create, switch, delete)
- Streaming chat with `<thinking>...</thinking>` folding
- Scene tab with scene info, todos, camera renders, and GLB preview
- Settings for API base URL and theme

### Development

```bash
cd web
npm install
npm run dev
```

### Configure backend URL by environment

The UI reads backend URL defaults from Vite env vars (build-time):

- `VITE_BACKEND_URL_DEV`: used in development mode (`npm run dev`)
- `VITE_BACKEND_URL_PROD`: used in production mode (`npm run build` / `npm run preview`)
- `VITE_BACKEND_URL`: shared fallback for both modes

Precedence is: mode-specific > shared > `http://localhost:8000`.

Create env files under `web/` as needed:

- `web/.env.development`
- `web/.env.production`

You can copy from `web/.env.example` as a starting point.

### Backend requirements

- Start the API server: `python main.py --mode api --port 8000`
- Ensure Blender addon is running (`BLENDER_HOST/BLENDER_PORT`, default `localhost:9876`)
- For headless mode testing, set `BLENDER_MODE=headless` in `.env` and see the root `README.md` for full setup steps.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Babel](https://babeljs.io/) (or [oxc](https://oxc.rs) when used in [rolldown-vite](https://vite.dev/guide/rolldown)) for Fast Refresh
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/) for Fast Refresh

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...

      // Remove tseslint.configs.recommended and replace with this
      tseslint.configs.recommendedTypeChecked,
      // Alternatively, use this for stricter rules
      tseslint.configs.strictTypeChecked,
      // Optionally, add this for stylistic rules
      tseslint.configs.stylisticTypeChecked,

      // Other configs...
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```

You can also install [eslint-plugin-react-x](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-x) and [eslint-plugin-react-dom](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-dom) for React-specific lint rules:

```js
// eslint.config.js
import reactX from 'eslint-plugin-react-x'
import reactDom from 'eslint-plugin-react-dom'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...
      // Enable lint rules for React
      reactX.configs['recommended-typescript'],
      // Enable lint rules for React DOM
      reactDom.configs.recommended,
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```
