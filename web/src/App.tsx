import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, type RouteObject, RouterProvider } from "react-router";
import { NotFound, RouteError } from "./components/RouteError";
import { AskPage } from "./features/ask/AskPage";
import { ChoosePage } from "./features/choose/ChoosePage";
import { LibraryPage } from "./features/library/LibraryPage";
import { DemoLimitPage } from "./features/limit/DemoLimitPage";
import { ResultsPage } from "./features/results/ResultsPage";

export const routes: RouteObject[] = [
  {
    errorElement: <RouteError />,
    children: [
      { path: "/", element: <ChoosePage /> },
      { path: "/p/:profileId", element: <LibraryPage /> },
      { path: "/p/:profileId/ask", element: <AskPage /> },
      { path: "/p/:profileId/results/:runId", element: <ResultsPage /> },
      { path: "/limit", element: <DemoLimitPage /> },
      { path: "*", element: <NotFound /> },
    ],
  },
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
