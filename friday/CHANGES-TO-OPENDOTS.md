# Changes to OpenDots files

Friday keeps edits to upstream OpenDots files small and marks code edits with `FRIDAY:`. Everything else Friday adds is in new files under `friday/`, `src/friday/` and `tests/friday/`.

| File                      | Change                                                                                                                 | Why                                                                                                       |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `src/server/index.ts`     | Model settings come from `fridayModelFromEnv`; the Obsidian vault links notes to contact names                         | `EVON_*` replaces `OPENAI_*` as a set, so Evon is the app's model                                         |
| `src/server/dot-agent.ts` | Local time; `fridayToolRounds`; `delegate_to_dot`; `fridayModelOptions`; `fridayToolTimeoutMs`; `fridayLoopStrategy`   | India time; chained tasks; helper Dots; fast replies (`FRIDAY_MODEL_OPTIONS`); quick actions end the turn |
| `src/server/platform.ts`  | `delegate` runs a helper Dot's task directly on this server; `fridayToolTimeoutMs`                                     | Helper Dots without remote round trips; the browser agent needs over 60 s                                 |
| `src/server/app.ts`       | Mounts `fridayVoiceRoutes`, `fridayActivityRoutes`, `fridayRunRoutes` and `fridayMemoryRoutes` under `/api`            | Gnani speech; live helper activity; fast direct voice turns                                               |
| `src/client/App.tsx`      | Friday's main page (`#/home`, the default), voice stage (`#/friday`) and desktop notch (`#/notch`); no inspector there | Friday's own screens                                                                                      |
| `vite.config.ts`          | Ignores `friday/services` and `data` in the watcher and dependency scan                                                | Service files changing must not reload the page mid-task                                                  |
| `index.html`              | Title, description and icon say Friday                                                                                 | The product is Friday                                                                                     |
| `src/client/Chat.tsx`     | Adds `<FridayVoice>` to the composer and `<FridaySteps>` above it                                                      | Push-to-talk and live steps in the classic chat                                                           |
| `tsconfig.server.json`    | Compiles `src/friday/server` and `src/friday/shared`                                                                   | Friday's server code is built with the app                                                                |
| `.env.example`            | A marked Friday block of variables                                                                                     | Evon, Gnani, services, time zone and turn limits                                                          |
| `README.md`               | A short Friday note at the top                                                                                         | Points readers to `friday/README.md`; the OpenDots README follows unchanged                               |

To see every change: `git diff main..friday -- src tests tsconfig.server.json vite.config.ts index.html .env.example README.md`
