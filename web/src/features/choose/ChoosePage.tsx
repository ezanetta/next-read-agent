import { useLocation, useNavigate } from "react-router";
import type { PickedBook } from "../../api/client";
import { ApiError, limitPath } from "../../api/errors";
import { useCreateManualProfile } from "../../api/queries";
import { Layout } from "../../components/Layout";
import { ui } from "../../components/ui";
import { BookSearch } from "./BookSearch";
import { GoodreadsUpload } from "./GoodreadsUpload";
import { PickedGrid } from "./PickedGrid";
import { MAX_PICKS, MIN_PICKS, usePicks } from "./picks";
import { SampleReaders } from "./SampleReaders";

/** Navigation state from the library's "Edit list": the books to start from. */
export type ChooseState = { picks?: PickedBook[] } | null;

export function ChoosePage() {
  const location = useLocation();
  const navigate = useNavigate();
  const initial = (location.state as ChooseState)?.picks;
  const { picks, add, remove, clear, canContinue } = usePicks(initial);
  const create = useCreateManualProfile();
  const pickedIds = new Set(picks.map((p) => p.source_id));

  function onContinue() {
    create.mutate(picks, {
      onSuccess: (profile) => {
        clear();
        navigate(`/p/${profile.id}`);
      },
      onError: (error) => {
        if (error instanceof ApiError && error.isDemoLimit) {
          navigate(limitPath(error.retryAfterSeconds));
        }
      },
    });
  }

  const missing = MIN_PICKS - picks.length;
  return (
    <Layout step={1}>
      <h1 className="max-w-4xl text-4xl leading-[1.05] font-bold tracking-tight sm:text-6xl">
        Three books you haven’t read, chosen from the ones you loved.
      </h1>
      <p className="mt-6 max-w-3xl text-lg text-muted sm:text-xl">
        An AI agent searches real catalogs, checks every pick against your library, and explains
        each choice with books you've read.
      </p>

      <div className="mt-10 grid gap-10 lg:grid-cols-[minmax(0,1fr)_360px] lg:gap-12">
        <section aria-labelledby="pick-heading" className={`${ui.panel} p-5 sm:p-9`}>
          <h2 id="pick-heading" className="text-2xl font-bold">
            Pick {MIN_PICKS} or more books you liked
          </h2>
          <p className="mt-2 text-muted">No account, no export. Your list expires after 3 days.</p>
          <div className="mt-6">
            <BookSearch pickedIds={pickedIds} onAdd={add} full={picks.length >= MAX_PICKS} />
          </div>
          <PickedGrid picks={picks} onRemove={remove} />
          <button
            type="button"
            disabled={!canContinue || create.isPending}
            onClick={onContinue}
            className={`${ui.primary} mt-8 w-full py-4 text-lg`}
          >
            {create.isPending
              ? "Saving your list…"
              : canContinue
                ? `Continue with these ${picks.length} books`
                : `Pick ${missing} more book${missing === 1 ? "" : "s"}`}
          </button>
          {create.isError && !(create.error instanceof ApiError && create.error.isDemoLimit) && (
            <p role="alert" className="mt-3 text-sm">
              {create.error instanceof ApiError
                ? create.error.message
                : "Your list couldn't be saved. Check your connection and try again."}
            </p>
          )}
        </section>

        <div className="space-y-6">
          <SampleReaders />
          <GoodreadsUpload />
        </div>
      </div>
    </Layout>
  );
}
