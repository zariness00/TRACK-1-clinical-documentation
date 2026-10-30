<!-- LOVABLE:BEGIN -->
> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.
<!-- LOVABLE:END -->

- All server access goes through src/lib/api.ts (USE_MOCK switch); mock data lives only in src/lib/mock.ts — keeps the switch to the real backend a one-line change.
- Signed-in profession lives in sessionStorage via src/lib/session.ts; gated routes use ssr:false + beforeLoad redirect — prototype has no real accounts.
- Task relevance is ranked in src/lib/relevance.ts using scenario topics, with stable input order for ties; it only arranges returned evidence and never creates clinical answers or asserts drug classes for unknown medicines.
- Source linking: document text is cleaned once (src/lib/segment.ts) and every displayed fact/claim must pass src/lib/verify.ts (quote location + value guards) before rendering — offsets always refer to the cleaned text, so nothing unverified reaches the screen.
