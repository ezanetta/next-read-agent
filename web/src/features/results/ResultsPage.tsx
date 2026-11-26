import { Link, useParams } from "react-router";
import { ErrorState } from "../../components/ErrorState";
import { Layout } from "../../components/Layout";
import { ui } from "../../components/ui";
import { AgentDidPanel } from "./AgentDidPanel";
import { PickCard } from "./PickCard";
import { loadResult } from "./resultStore";

export function ResultsPage() {
  const { profileId = "", runId = "" } = useParams();
  const stored = loadResult(runId);

  if (!stored) {
    return (
      <Layout step={3}>
        <ErrorState
          title="These results are gone"
          action={
            <Link to={`/p/${profileId}/ask`} className={ui.primary}>
              Ask again
            </Link>
          }
        >
          Results are kept only in this browser tab. Ask again to get new picks.
        </ErrorState>
      </Layout>
    );
  }

  const { result, request } = stored;
  const ask = (
    <Link to={`/p/${profileId}/ask`} className={ui.ghost}>
      Ask for something else
    </Link>
  );
  return (
    <Layout step={3}>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-muted">
            {request ? (
              <>
                You asked for <span className="text-text">“{request}”</span>
              </>
            ) : (
              "Picked from your books alone"
            )}
          </p>
          <h1 className="mt-2 text-4xl font-bold sm:text-6xl">Your next three reads</h1>
        </div>
        <div className="hidden sm:block">{ask}</div>
      </div>
      <div className="mt-10 grid gap-6 lg:grid-cols-3">
        {result.books.map((book, index) => (
          <PickCard key={book.title} book={book} index={index} profileId={profileId} />
        ))}
      </div>
      <div className="mt-8">
        <AgentDidPanel trace={result.trace} />
      </div>
      <Link to={`/p/${profileId}/ask`} className={`${ui.primary} mt-6 w-full sm:hidden`}>
        Ask for something else
      </Link>
    </Layout>
  );
}
