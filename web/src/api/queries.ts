import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import {
  api,
  type LibraryBook,
  type PickedBook,
  type ProfileSummary,
  type SearchHit,
} from "./client";
import { toApiError } from "./errors";

export const MIN_QUERY = 2;

export function useSearchBooks(query: string) {
  const q = query.trim();
  return useQuery({
    queryKey: ["search", q.toLowerCase()],
    enabled: q.length >= MIN_QUERY,
    placeholderData: keepPreviousData,
    staleTime: 10 * 60_000,
    queryFn: async ({ signal }): Promise<SearchHit[]> => {
      const { data, error, response } = await api.GET("/books/search", {
        params: { query: { q } },
        signal,
      });
      if (error !== undefined || !data) throw toApiError(response, error);
      return data.books;
    },
  });
}

export function usePresets() {
  return useQuery({
    queryKey: ["presets"],
    staleTime: Number.POSITIVE_INFINITY,
    queryFn: async (): Promise<ProfileSummary[]> => {
      const { data, error, response } = await api.GET("/profiles/presets");
      if (error !== undefined || !data) throw toApiError(response, error);
      return data;
    },
  });
}

export function useProfile(profileId: string) {
  return useQuery({
    queryKey: ["profile", profileId],
    queryFn: async (): Promise<ProfileSummary> => {
      const { data, error, response } = await api.GET("/profiles/{profile_id}", {
        params: { path: { profile_id: profileId } },
      });
      if (error !== undefined || !data) throw toApiError(response, error);
      return data;
    },
  });
}

export function useProfileBooks(profileId: string) {
  return useQuery({
    queryKey: ["profile", profileId, "books"],
    queryFn: async (): Promise<LibraryBook[]> => {
      const { data, error, response } = await api.GET("/profiles/{profile_id}/books", {
        params: { path: { profile_id: profileId } },
      });
      if (error !== undefined || !data) throw toApiError(response, error);
      return data.books;
    },
  });
}

export function useCreateManualProfile() {
  return useMutation({
    mutationFn: async (books: PickedBook[]): Promise<ProfileSummary> => {
      const { data, error, response } = await api.POST("/profiles/manual", { body: { books } });
      if (error !== undefined || !data) throw toApiError(response, error);
      return data;
    },
  });
}

export function useUploadGoodreads() {
  return useMutation({
    mutationFn: async (file: File): Promise<ProfileSummary> => {
      const { data, error, response } = await api.POST("/profiles/goodreads", {
        // The schema types the file as a string (binary); send it as multipart.
        body: { file: file as unknown as string },
        bodySerializer: (body) => {
          const form = new FormData();
          form.append("file", body.file as unknown as Blob);
          return form;
        },
      });
      if (error !== undefined || !data) throw toApiError(response, error);
      return data;
    },
  });
}
