import { useId, useRef } from "react";
import { useNavigate } from "react-router";
import { ApiError, limitPath } from "../../api/errors";
import { useUploadGoodreads } from "../../api/queries";
import { ui } from "../../components/ui";

const GOODREADS_EXPORT_URL = "https://www.goodreads.com/review/import";

export function GoodreadsUpload() {
  const upload = useUploadGoodreads();
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();

  function onFile(file: File | undefined) {
    if (!file) return;
    upload.mutate(file, {
      onSuccess: (profile) => navigate(`/p/${profile.id}`),
      onError: (error) => {
        if (error instanceof ApiError && error.isDemoLimit) {
          navigate(limitPath(error.retryAfterSeconds));
        }
      },
    });
    if (inputRef.current) inputRef.current.value = "";
  }

  return (
    <section
      aria-labelledby="goodreads-heading"
      className="rounded-2xl border border-dashed border-border-strong p-6 text-center"
    >
      <h2 id="goodreads-heading" className="font-sans text-lg font-semibold">
        Have Goodreads? Upload your export
      </h2>
      <p className="mt-2 text-sm text-muted">
        CSV, up to 1 MB. Only titles, authors, ratings and shelves are read.
      </p>
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        accept=".csv,text/csv"
        className="sr-only"
        onChange={(e) => onFile(e.target.files?.[0])}
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={upload.isPending}
        className={`${ui.secondary} mt-4`}
      >
        {upload.isPending ? "Reading your library…" : "Choose CSV file"}
      </button>
      {upload.isError && !(upload.error instanceof ApiError && upload.error.isDemoLimit) && (
        <p role="alert" className="mt-3 text-sm text-text">
          {upload.error instanceof ApiError
            ? upload.error.message
            : "The upload failed. Check your connection and try again."}
        </p>
      )}
      <p className="mt-4">
        <a href={GOODREADS_EXPORT_URL} className={`${ui.link} text-sm`}>
          How to export from Goodreads
        </a>
      </p>
    </section>
  );
}
