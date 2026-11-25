import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, type RouteObject, RouterProvider } from "react-router";
import { Layout } from "./components/Layout";
import { AskPage } from "./features/ask/AskPage";
import { ChoosePage } from "./features/choose/ChoosePage";
import { LibraryPage } from "./features/library/LibraryPage";
import { ResultsPage } from "./features/results/ResultsPage";

function Placeholder({ title }: { title: string }) {
  return (
    <Layout step={1}>
      <h1 className="text-4xl font-bold">{title}</h1>
    </Layout>
  );
}

export const routes: RouteObject[] = [
  { path: "/", element: <ChoosePage /> },
  { path: "/p/:profileId", element: <LibraryPage /> },
  { path: "/p/:profileId/ask", element: <AskPage /> },
  { path: "/p/:profileId/results/:runId", element: <ResultsPage /> },
  { path: "/limit", element: <Placeholder title="Demo limit" /> },
];

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: 1, staleTime: 60_000, refetchOnWindowFocus: false } },
  });
}

const router = createBrowserRouter(routes);
const queryClient = makeQueryClient();

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
