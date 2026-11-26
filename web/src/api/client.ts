import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

/** The API's base URL: the Vite proxy in dev, VITE_API_BASE_URL in production. */
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "/api";

// fetch is looked up on each call (not captured once), so tests can replace it.
export const api = createClient<paths>({
  baseUrl: API_BASE_URL,
  fetch: (request) => globalThis.fetch(request),
});

export type Schemas = components["schemas"];
export type ProfileSummary = Schemas["ProfileSummary"];
export type LibraryBook = Schemas["LibraryBook"];
export type LibraryPage = Schemas["LibraryPage"];
export type SearchHit = Schemas["SearchHit"];
export type PickedBook = Schemas["PickedBook"];
export type ErrorBody = Schemas["ErrorBody"];
export type Filters = Schemas["Filters"];
export type FilterName = "language" | "max_pages" | "min_pages";
export type RecommendationEvent = Schemas["RecommendationEvent"];
export type RecommendationResult = Schemas["RecommendationResult"];
export type RecommendedBook = Schemas["RecommendedBook"];
export type TraceSummary = Schemas["TraceSummary"];
export type ProblemView = Schemas["ProblemView"];
