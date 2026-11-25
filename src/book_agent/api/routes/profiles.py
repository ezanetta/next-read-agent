"""Profiles: create from a manual list or a Goodreads upload, presets, library view."""

from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status

from book_agent.api.dependencies import Services
from book_agent.api.schemas import (
    ErrorBody,
    LibraryPage,
    ManualProfileRequest,
    ProfileSummary,
    Shelf,
)
from book_agent.api.views import library_books, profile_summary
from book_agent.demo_profiles import (
    CSV_MAX_BYTES,
    find_profile,
    goodreads_profile,
    manual_profile,
    store_temporary,
)
from book_agent.domain.profiles import StoredProfile

router = APIRouter(prefix="/profiles", tags=["profiles"])

Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"model": ErrorBody, "description": "No such profile, or it expired"}}
INVALID: Responses = {
    400: {"model": ErrorBody, "description": "The profile can't be made from this"}
}


def not_found() -> HTTPException:
    return HTTPException(404, detail={"error": "not_found", "message": "profile not found"})


def _get(services: Services, profile_id: str) -> StoredProfile:
    stored = find_profile(profile_id, services.presets, services.profiles, services.clock())
    if stored is None:
        raise not_found()
    return stored


@router.post(
    "/manual",
    operation_id="createManualProfile",
    status_code=status.HTTP_201_CREATED,
    responses=INVALID,
)
async def create_manual_profile(body: ManualProfileRequest, services: Services) -> ProfileSummary:
    """A temporary profile from 5-50 books picked through search, all counted as liked."""
    profile, covers = manual_profile(body.books)
    stored = store_temporary(
        services.profiles, "manual", profile, services.clock(),
        covers=covers, ttl=services.profile_ttl,
    )  # fmt: skip
    return profile_summary(stored)


@router.post(
    "/goodreads",
    operation_id="createGoodreadsProfile",
    status_code=status.HTTP_201_CREATED,
    responses=INVALID,
)
async def create_goodreads_profile(
    file: Annotated[UploadFile, File(description="Goodreads 'Export Library' CSV, up to 1 MB")],
    services: Services,
) -> ProfileSummary:
    """A temporary profile from a Goodreads export. The file is parsed and discarded."""
    content = await file.read(CSV_MAX_BYTES + 1)
    profile = goodreads_profile(content)
    stored = store_temporary(
        services.profiles, "goodreads", profile, services.clock(), ttl=services.profile_ttl
    )
    return profile_summary(stored)


@router.get("/presets", operation_id="listPresetProfiles")
async def list_preset_profiles(services: Services) -> list[ProfileSummary]:
    """The sample profiles, and the owner's library when it's configured."""
    return [profile_summary(p) for p in services.presets.values()]


@router.get("/{profile_id}", operation_id="getProfile", responses=NOT_FOUND)
async def get_profile(profile_id: str, services: Services) -> ProfileSummary:
    return profile_summary(_get(services, profile_id))


@router.get("/{profile_id}/books", operation_id="listProfileBooks", responses=NOT_FOUND)
async def list_profile_books(
    profile_id: str, services: Services, shelf: Annotated[Shelf | None, Query()] = None
) -> LibraryPage:
    """The library view: every book with cover, rating (none when picked) and shelf."""
    return LibraryPage(
        profile_id=profile_id, books=library_books(_get(services, profile_id), shelf)
    )


@router.delete(
    "/{profile_id}",
    operation_id="deleteProfile",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**NOT_FOUND, 400: {"model": ErrorBody, "description": "Presets can't be deleted"}},
)
async def delete_profile(profile_id: str, services: Services) -> None:
    """Delete a temporary profile now instead of waiting for it to expire."""
    if profile_id in services.presets:
        raise HTTPException(400, detail={"error": "preset", "message": "presets can't be deleted"})
    if not services.profiles.delete(profile_id):
        raise not_found()
