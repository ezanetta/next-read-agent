import { ErrorState } from "./ErrorState";
import { Layout } from "./Layout";

/** Unknown URLs and unexpected errors: say so plainly and offer a way back. */
export function NotFound() {
  return (
    <Layout>
      <ErrorState title="This page doesn't exist">
        The link may be old or mistyped. Start again from your books.
      </ErrorState>
    </Layout>
  );
}

export function RouteError() {
  return (
    <Layout>
      <ErrorState title="Something went wrong">
        The page hit an unexpected error. Reload it, or start again from your books.
      </ErrorState>
    </Layout>
  );
}
