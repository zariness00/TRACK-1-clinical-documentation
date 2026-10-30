# Screenshot to Site

Implement exactly the screenshot and nothing else

This project was built with [Lovable](https://lovable.dev).

## Build with Lovable

Continue developing this project in the [Lovable editor](https://lovable.dev/projects/5974a88f-60a8-4271-ae38-b69725d9071b).

- **Ship faster**: describe what you want to build and Lovable handles the code.
- **Stay in sync**: every change made in Lovable is committed straight to this repository.
- **Full ownership**: this code is yours. Push to `main` on GitHub and your changes sync back into Lovable, ready for your next prompt.

## Development

Prefer working locally? You need Node.js and npm — [install with nvm](https://github.com/nvm-sh/nvm#installing-and-updating).

```sh
git clone <this-repository-url>
cd <repository-name>
npm i
npm run dev
```

## Backend hand-off notes

- **Task options are temporary.** The task field in the patient view offers preset tasks from `QUICK` in `src/lib/session.ts`. These are prototype placeholders only: once the backend is connected, the orchestrator analyses the free-text task, so remove the preset list (`TaskBar` in `src/components/clinical/ClinicalApp.tsx`) and keep only the text field.
- All server access goes through `src/lib/api.ts`; set `USE_MOCK = false` to use the real backend (this also hides the MOCK DATA tag).
