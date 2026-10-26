import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// Next.js 16 removed `next lint`; this is the flat config create-next-app ships.
export default defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    // React Compiler-oriented checks new in eslint-plugin-react-hooks 7. The
    // codebase predates them: 26 `setLoading(true); fetch(...)`-style effects
    // and Date.now() inside event handlers (reported as impure renders). Kept
    // visible as warnings; refactor component by component, then promote back
    // to errors.
    rules: {
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/purity": "warn",
      "react-hooks/refs": "warn",
    },
  },
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts", "node_modules/**"]),
]);
