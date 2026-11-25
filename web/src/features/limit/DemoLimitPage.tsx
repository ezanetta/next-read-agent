import { useSearchParams } from "react-router";
import { Layout } from "../../components/Layout";
import { ui } from "../../components/ui";
import { waitTime } from "../../lib/format";
import { DEMO_VIDEO_URL, HOW_IT_WORKS_URL } from "../../lib/links";

function Shelves() {
  return (
    <div aria-hidden="true" className="flex h-24 items-end gap-2 sm:h-36">
      <span className="h-[88%] w-8 rounded-md bg-[#2e4a7a] sm:w-12" />
      <span className="h-[75%] w-7 rounded-md bg-[#8a5a2e] sm:w-10" />
      <span className="h-full w-9 rounded-md bg-[#2f5d50] sm:w-14" />
      <span className="ml-1 h-[85%] w-8 origin-bottom-left rotate-12 rounded-md bg-[#6b4e8c] sm:w-12" />
    </div>
  );
}

/** Shown on HTTP 429 or a failed event of kind demo_limit, with the retry time when the
 * API gives one (?retry=seconds). */
export function DemoLimitPage() {
  const [params] = useSearchParams();
  const retry = Number(params.get("retry"));
  const hasRetry = Number.isFinite(retry) && retry > 0;

  return (
    <Layout>
      <div className="mx-auto max-w-4xl">
        <Shelves />
        <p className="mt-10 text-sm font-semibold tracking-widest text-accent">
          DEMO LIMIT REACHED
        </p>
        <h1 className="mt-3 text-4xl font-bold leading-tight sm:text-6xl">
          The shelves are closed for today.
        </h1>
        <p className="mt-5 max-w-3xl text-lg text-muted sm:text-xl">
          This demo runs on free model quotas, so it serves a limited number of recommendations each
          day. You've used yours, or today's are all taken.
        </p>
        <div className="mt-6 flex flex-wrap gap-3 text-sm">
          {hasRetry && (
            <span className="rounded-full bg-surface-2 px-4 py-2">
              Try again in {waitTime(retry)}
            </span>
          )}
          <span className="rounded-full border border-dashed border-border-strong px-4 py-2 text-muted">
            Your book list is saved for 3 days
          </span>
        </div>
        <div className="mt-8 flex flex-wrap gap-4">
          {DEMO_VIDEO_URL && (
            <a href={DEMO_VIDEO_URL} className={ui.primary}>
              Watch a 2-minute demo
            </a>
          )}
          <a href={HOW_IT_WORKS_URL} className={DEMO_VIDEO_URL ? ui.ghost : ui.primary}>
            Read how it works
          </a>
        </div>
        <section className={`${ui.panel} mt-12 p-6 sm:p-8`}>
          <h2 className="font-sans text-lg font-semibold">Run it yourself</h2>
          <p className="mt-2 text-muted">
            The code is open source. With a free Gemini API key it runs on your machine, with your
            own Goodreads export.
          </p>
        </section>
      </div>
    </Layout>
  );
}
