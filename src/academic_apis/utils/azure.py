import asyncio
import logging
import os
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from glob import glob
from typing import Any

import aiofiles
from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.identity.aio import DefaultAzureCredential as DefaultAzureCredentialAsync
from azure.storage.blob import (
    BlobServiceClient,
    ContainerSasPermissions,
    UserDelegationKey,
    generate_container_sas,
)
from azure.storage.blob.aio import BlobServiceClient as BlobServiceClientAsync

from academic_apis.utils import settings
from academic_apis.utils.types import AzureAccessLinks

logger = logging.getLogger(__name__)


def get_client() -> BlobServiceClient:
    if settings.azure_storage_use_entra:
        credential = DefaultAzureCredential()
        return BlobServiceClient(
            account_url=f"https://{settings.azure_storage_account_name}.blob.core.windows.net",
            credential=credential,
        )
    return BlobServiceClient.from_connection_string(
        conn_str=settings.azure_storage_connection_string
    )


@asynccontextmanager
async def async_client() -> AsyncIterator[BlobServiceClientAsync]:
    if settings.azure_storage_use_entra:
        async with DefaultAzureCredentialAsync() as credential:  # noqa: SIM117
            async with BlobServiceClientAsync(
                account_url=f"https://{settings.azure_storage_account_name}.blob.core.windows.net",
                credential=credential,
            ) as client:
                yield client
    else:
        async with BlobServiceClientAsync.from_connection_string(
            conn_str=settings.azure_storage_connection_string
        ) as blob_service_client:
            yield blob_service_client


async def upload_single_file_if_missing(
    client: BlobServiceClientAsync,
    file_path: str,
    blob_path: str,
) -> None:
    """Uploads a single file asynchronously, skipping if it already exists."""
    blob_client = client.get_blob_client(
        container=settings.azure_storage_container_name, blob=blob_path
    )

    # Fast existence check to avoid reading local disk or making full POST requests
    if await blob_client.exists():
        logger.warning(f"Skipped {file_path} (already exists)")
        return

    # Read local file in chunks using aiofiles to avoid blocking the event loop
    async with aiofiles.open(file_path, "rb") as f:
        try:
            logger.info(f"Uploading {file_path}...")
            # overwrite=False ensures a race condition won't overwrite an existing blob
            await blob_client.upload_blob(f, overwrite=False)
        except ResourceExistsError:
            logger.warning(f"Skipped {file_path} (already existed)")


async def sync_directory(
    source_dir: str,  # not using Path here, discouraged for async functions: https://docs.astral.sh/ruff/rules/blocking-path-method-in-async-function/
    target_dir: str,
) -> None:
    logger.info(f"Synchronising from {source_dir} to {target_dir}")
    async with async_client() as client:
        upload_tasks = []

        for root, sub_paths, files in os.walk(source_dir):
            if len(sub_paths) > 0:
                continue
            for file_name in files:
                file_path = os.path.join(root, file_name)  # noqa: PTH118
                relative_path = file_path[len(source_dir) + 1 :]
                blob_path = f"{settings.azure_storage_container_path}{target_dir}/{relative_path}"  # `azure_storage_container_path` always ends with '/'

                upload_tasks.append(
                    upload_single_file_if_missing(client, file_path, blob_path)
                )

        # 3. Use a Semaphore to limit parallel uploads and avoid socket starvation
        semaphore = asyncio.Semaphore(settings.azure_storage_concurrency)

        async def sema_task(task: Coroutine[Any, Any, None]) -> None:
            async with semaphore:
                await task

        # 4. Run all file sync checks concurrently
        await asyncio.gather(*(sema_task(t) for t in upload_tasks))


async def _read_concatenated_files(
    file_paths: list[str], include_file_headers: bool = False
) -> AsyncIterator[bytes]:
    """
    Streams the contents of `file_paths` line by line, one file after another.

    Lines are buffered into chunks of up to `buffer_size` bytes, so memory usage stays
    constant no matter how large the individual files are. Newlines are inserted where
    one is missing between files, so that lines of neighbouring files are never joined
    and the concatenated stream always ends with a newline.
    """
    buffer = bytearray()
    ends_with_newline = True
    for file_path in file_paths:
        logger.info(f"Streaming {file_path}...")
        if include_file_headers:
            buffer += (
                b"\n+-----------------------------------------------------+\n"
                + file_path.encode()
                + b"\n+-----------------------------------------------------+\n"
            )
        async with aiofiles.open(file_path, "rb") as f:
            async for line in f:
                if not ends_with_newline:
                    buffer += b"\n"
                buffer += line
                ends_with_newline = line.endswith(b"\n")
                if len(buffer) >= settings.azure_storage_buffer_size:
                    yield bytes(buffer)
                    buffer = bytearray()
    if not ends_with_newline:
        buffer += b"\n"
    if buffer:
        yield bytes(buffer)


async def upload_concatenated_files(
    source_pattern: str,
    target_file: str,
    overwrite: bool = False,
    include_file_headers: bool = False,
) -> None:
    """
    Uploads all files matching `pattern` below `source_dir` as one concatenated blob.

    Matching files are concatenated in sorted order and streamed line by line into a
    single target file, so memory usage stays constant even if no single file fits
    into it. The upload is skipped if the blob already exists.
    """
    blob_path = f"{settings.azure_storage_container_path}{target_file}"  # `azure_storage_container_path` always ends with '/'
    file_paths = sorted(glob(source_pattern))  # noqa: PTH207
    logger.info(
        f"Identified {len(file_paths):,} files to concatenate and upload to {blob_path} from {source_pattern}"
    )

    if not file_paths:
        logger.warning(f"No files matching {source_pattern}, nothing uploaded")
        return

    async with async_client() as client:
        blob_client = client.get_blob_client(
            container=settings.azure_storage_container_name,
            blob=blob_path,
        )

        try:
            await blob_client.upload_blob(
                _read_concatenated_files(
                    file_paths, include_file_headers=include_file_headers
                ),
                overwrite=overwrite,
            )
        except ResourceExistsError:
            logger.warning(f"Skipped {blob_path} (already existed)")


async def get_azure_links(blob_path: str) -> AzureAccessLinks:
    # Give the SAS token a longer TTL (e.g., 2 hours) so the user has time to wait + download
    time_start = datetime.now(UTC)
    time_expire = time_start + timedelta(minutes=settings.azure_storage_key_lifetime)
    time_start = time_start - timedelta(minutes=5)

    prefix = f"{settings.azure_storage_container_path}{blob_path}"
    async with async_client() as client:
        account_key: str | None = None
        user_delegation_key: UserDelegationKey | None = None
        if settings.azure_storage_use_entra:
            user_delegation_key = await client.get_user_delegation_key(
                key_start_time=time_start,
                key_expiry_time=time_expire,
            )
        else:
            account_key = settings.azure_storage_account_key

        sas_token = generate_container_sas(
            account_name=settings.azure_storage_account_name,
            container_name=settings.azure_storage_container_name,
            account_key=account_key,
            user_delegation_key=user_delegation_key,
            permission=ContainerSasPermissions(read=True, list=True),
            start=time_start,
            expiry=time_expire,
            blob_name_starts_with=prefix,
        )

        base_url = f"https://{settings.azure_storage_account_name}.blob.core.windows.net/{settings.azure_storage_container_name}"
        return AzureAccessLinks(
            download_url=f"{base_url}/{prefix}?{sas_token}",
            list_url=f"{base_url}?restype=container&comp=list&prefix={prefix}&{sas_token}",
            responses_url=f"{base_url}/{prefix}/responses.jsonl?{sas_token}",
            log_url=f"{base_url}/{prefix}/responses.log?{sas_token}",
            errors_url=f"{base_url}/{prefix}/errors.log?{sas_token}",
        )
