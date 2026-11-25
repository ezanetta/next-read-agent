import { type FormEvent, type KeyboardEvent, useEffect, useId, useState } from "react";
import { useNavigate, useParams } from "react-router";
import type { FilterName, ProfileSummary } from "../../api/client";
import { limitPath } from "../../api/errors";
import { useProfile } from "../../api/queries";
import { Cover } from "../../components/Cover";
import { ErrorState } from "../../components/ErrorState";
import { Layout } from "../../components/Layout";
import { ui } from "../../components/ui";
import { presetTitle } from "../choose/SampleReaders";
import { saveResult } from "../results/resultStore";
import { FailurePanel } from "./FailurePanel";
import { ProgressSteps } from "./ProgressSteps";
import { UnderstoodChips } from "./UnderstoodChips";
import { useRecommendationStream } from "./useRecommendationStream";

export const MAX_REQUEST = 500;
export const EXAMPLES = [
  "something like East of Eden",
  "a sci-fi book that isn't about space",
  "cuentos como Borges, en español",
] as const;

function usingLabel(profile: ProfileSummary): string {
  switch (profile.source) {
    case "manual":
      return `Using your ${profile.read} picked books`;
    case "sample":
      return `Using the ${presetTitle(profile)} reader · ${profile.read} books`;
    case "owner":
      return "Using the author's library";
    default:
      return `Using your Goodreads library · ${profile.read} books read`;
  }
}

function useElapsed(running: boolean): number {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!running) return;
    const started = Date.now();
    setElapsed(0);
    const timer = setInterval(() => setElapsed((Date.now() - started) / 1000), 200);
    return () => clearInterval(timer);
  }, [running]);
  return elapsed;
}

export function AskPage() {
  const { profileId = "" } = useParams();
  const navigate = useNavigate();
  const profile = useProfile(profileId);
  const { progress, start } = useRecommendationStream(profileId, (retry) =>
    navigate(limitPath(retry)),
  );
  const [text, setText] = useState("");
  const [asked, setAsked] = useState<{ request: string | null; ignored: FilterName[] } | null>(
    null,
  );
  const running = progress.status === "running";
  const elapsed = useElapsed(running);
  const fieldId = useId();

  // Done: keep the result for a reload and show it (D-045).
  useEffect(() => {
    if (progress.status !== "done" || !progress.result || !progress.runId) return;
    saveResult(progress.runId, {
      profileId,
      request: asked?.request ?? null,
      result: progress.result,
    });
    navigate(`/p/${profileId}/results/${progress.runId}`);
  }, [progress.status, progress.result, progress.runId, profileId, asked, navigate]);

  useEffect(() => {
    if (progress.failure?.kind === "demo_limit") navigate("/limit");
  }, [progress.failure, navigate]);

  function ask(request: string | null, ignored: FilterName[] = []) {
    setAsked({ request, ignored });
    void start(request, ignored);
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (running) return;
    ask(text.trim() || null);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  if (profile.isError) {
    return (
      <Layout step={2}>
        <ErrorState title="This list has expired">
          Lists are kept for 3 days. Pick your books again, or start from a sample reader.
        </ErrorState>
      </Layout>
    );
  }

  return (
    <Layout step={2}>
      <div className="mx-auto max-w-4xl">
        {profile.data && (
          <div className="flex items-center gap-4">
            <div className="flex -space-x-1" aria-hidden="true">
              {(profile.data.cover_urls.length > 0
                ? profile.data.cover_urls
                : [null, null, null]
              ).map((url, i) => (
                <Cover key={url ?? i} title={`${profileId}-${i}`} url={url} size="strip" />
              ))}
            </div>
            <p className="text-muted">{usingLabel(profile.data)}</p>
          </div>
        )}
        <h1 className="mt-6 text-4xl font-bold sm:text-6xl">What do you feel like reading?</h1>

        <form onSubmit={onSubmit} className="mt-8">
          <label htmlFor={fieldId} className="sr-only">
            Your request
          </label>
          <div className="flex flex-col gap-3 sm:relative">
            <textarea
              id={fieldId}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={onKeyDown}
              maxLength={MAX_REQUEST}
              rows={2}
              placeholder="e.g. a short, melancholic novel"
              className="min-h-28 w-full resize-none rounded-2xl border border-border-strong bg-surface px-6 py-5 text-lg text-text placeholder:text-muted focus:border-accent focus:outline-none sm:pr-48"
            />
            <button
              type="submit"
              disabled={running}
              className={`${ui.primary} w-full py-4 sm:absolute sm:top-1/2 sm:right-4 sm:w-auto sm:-translate-y-1/2`}
            >
              {running ? "Working…" : "Recommend"}
            </button>
          </div>
          <p className="mt-2 text-sm text-muted">
            Leave it empty to get picks from your books alone.
          </p>
        </form>

        {progress.understood && (
          <UnderstoodChips
            understood={progress.understood}
            onRemoveFilter={(name) =>
              ask(asked?.request ?? null, [...(asked?.ignored ?? []), name])
            }
          />
        )}

        <div className="mt-8 space-y-6">
          {progress.status !== "idle" && <ProgressSteps progress={progress} elapsed={elapsed} />}
          {progress.status === "failed" &&
            progress.failure &&
            progress.failure.kind !== "demo_limit" && (
              <FailurePanel
                kind={progress.failure.kind}
                message={progress.failure.message}
                onRetry={() => ask(asked?.request ?? null, asked?.ignored ?? [])}
              />
            )}
        </div>

        <p className="mt-10 text-muted">
          Try:{" "}
          {EXAMPLES.map((example, i) => (
            <span key={example}>
              {i > 0 && " · "}
              <button
                type="button"
                disabled={running}
                onClick={() => {
                  setText(example);
                  ask(example);
                }}
                className={`${ui.link} disabled:cursor-not-allowed disabled:no-underline`}
              >
                {example}
              </button>
            </span>
          ))}
        </p>
      </div>
    </Layout>
  );
}
