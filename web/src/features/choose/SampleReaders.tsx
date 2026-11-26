import { Link } from "react-router";
import type { ProfileSummary } from "../../api/client";
import { usePresets } from "../../api/queries";
import { Cover } from "../../components/Cover";

/** Short titles for the sample readers, as the mockups show them. */
const SAMPLE_TITLES: Record<string, string> = {
  "sample-classics": "Classics",
  "sample-sf-fantasy": "Science fiction & fantasy",
  "sample-mystery": "Mystery & thriller",
  "sample-latin-american": "Latin American literature",
  "sample-nonfiction": "Popular non-fiction",
};

export function presetTitle(profile: ProfileSummary): string {
  return SAMPLE_TITLES[profile.id] ?? profile.name;
}

function surname(author: string): string {
  return author.trim().split(/\s+/).at(-1) ?? author;
}

function CoverStrip({ profile }: { profile: ProfileSummary }) {
  const covers = profile.cover_urls.length > 0 ? profile.cover_urls : [null, null, null];
  return (
    <div className="flex shrink-0 -space-x-2" aria-hidden="true">
      {covers.slice(0, 3).map((url, i) => (
        <Cover key={url ?? i} title={`${profile.id}-${i}`} url={url} size="thumb" />
      ))}
    </div>
  );
}

export function SampleReaders() {
  const presets = usePresets();
  const samples = (presets.data ?? []).filter((p) => p.source === "sample");
  const owner = presets.data?.find((p) => p.source === "owner");

  return (
    <section aria-labelledby="samples-heading">
      <h2 id="samples-heading" className="text-xl font-bold">
        Or start from a sample reader
      </h2>
      {presets.isError && (
        <p role="alert" className="mt-3 text-sm text-muted">
          The sample readers couldn't load. Is the API running?
        </p>
      )}
      <ul className="mt-4 space-y-4">
        {samples.map((profile) => (
          <li key={profile.id}>
            <Link
              to={`/p/${profile.id}`}
              className="flex items-center gap-4 rounded-2xl border border-border bg-surface p-4 transition hover:border-border-strong"
            >
              <CoverStrip profile={profile} />
              <span className="min-w-0 flex-1">
                <span className="block font-semibold text-lg wrap-break-word">
                  {presetTitle(profile)}
                </span>
                <span className="block text-sm text-muted">
                  {profile.read} books
                  <span className="hidden sm:inline">
                    {profile.top_authors.length > 0 &&
                      ` · ${profile.top_authors.map(surname).join(", ")}`}
                  </span>
                </span>
              </span>
              <span aria-hidden="true" className="text-muted">
                →
              </span>
            </Link>
          </li>
        ))}
        {owner && (
          <li>
            <Link
              to={`/p/${owner.id}`}
              className="flex items-center gap-4 rounded-2xl border border-accent/30 bg-accent/5 p-4 transition hover:border-accent/60"
            >
              <span className="min-w-0 flex-1">
                <span className="block font-semibold text-lg">The author's library</span>
                <span className="block text-sm text-muted">
                  Real Goodreads ratings · the library the evals use
                </span>
              </span>
              <span aria-hidden="true" className="text-accent">
                →
              </span>
            </Link>
          </li>
        )}
      </ul>
    </section>
  );
}
