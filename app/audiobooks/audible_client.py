"""
Audible client wrapper for authentication, library access, and downloads.

Uses the 'audible' Python library to interact with Audible's API.
"""
import os
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, List, Any

log = logging.getLogger(__name__)

try:
    import audible
    from audible.localization import Locale
    _HAVE_AUDIBLE = True
except Exception:
    audible = None
    Locale = None
    _HAVE_AUDIBLE = False

CLIENT_HEADERS = {
    "User-Agent": "Audible/671 CFNetwork/1240.0.4 Darwin/20.6.0"
}

class AudibleClient:
    """Wrapper for Audible API client with authentication and library access."""

    def __init__(self, auth_file: Optional[str] = None):
        """Initialize Audible client.
        
        Args:
            auth_file: Path to audible auth file. If None, uses data/audible_auth.json
        """
        if not _HAVE_AUDIBLE:
            raise RuntimeError('audible library not available; install via pip install audible')
        
        if auth_file is None:
            # default to data/ folder
            data_dir = Path(__file__).parent.parent.parent / 'data'
            data_dir.mkdir(parents=True, exist_ok=True)
            auth_file = str(data_dir / 'audible_auth.json')
        
        self.auth_file = auth_file
        self._client = None
        self._lock = threading.Lock()
        self._auth_valid = True  # Track if existing auth is valid
        
        # Try to load existing auth
        if os.path.exists(self.auth_file):
            try:
                auth = audible.Authenticator.from_file(self.auth_file)
                self._client = audible.Client(auth=auth)
                log.info('Loaded Audible auth from %s', self.auth_file)
            except Exception as e:
                log.warning('Failed to load Audible auth from %s: %s', self.auth_file, e)
                self._client = None
                self._auth_valid = False
                # Remove corrupted auth file
                try:
                    os.remove(self.auth_file)
                    log.info('Removed invalid auth file')
                except Exception:
                    log.exception('Failed to remove invalid auth file')

    def is_authenticated(self) -> bool:
        """Check if client is authenticated and ready to use."""
        with self._lock:
            return self._client is not None
    
    def needs_reauthentication(self) -> bool:
        """Check if authentication file was invalid and needs reauth."""
        with self._lock:
            return not self._auth_valid and self._client is None

    def authenticate(self, username: str, password: str, country_code: str = 'us', otp_code: Optional[str] = None) -> Dict[str, Any]:
        """Authenticate with Audible and save credentials.
        
        Args:
            username: Audible/Amazon username/email
            password: Account password
            country_code: Audible marketplace (us, uk, de, fr, etc.)
            otp_code: Optional one-time password for 2FA
            
        Returns:
            dict with 'success': bool, optional 'error': str, or 'requires_otp': True
        """
        try:
            with self._lock:
                # Flag to track if OTP was requested
                otp_requested = {'value': False}
                
                # OTP callback that signals when 2FA is needed
                def otp_callback():
                    if otp_code:
                        return otp_code
                    else:
                        # Mark that OTP was requested and raise to abort
                        otp_requested['value'] = True
                        raise Exception("OTP_REQUIRED_BY_USER")
                
                # Authenticate and save to file
                try:
                    auth = audible.Authenticator.from_login(
                        username=username,
                        password=password,
                        locale=Locale(country_code),
                        with_username=False,
                        otp_callback=otp_callback
                    )
                except Exception as e:
                    # Check if we raised the OTP required exception
                    if otp_requested['value'] or "OTP_REQUIRED_BY_USER" in str(e):
                        return {'success': False, 'requires_otp': True, 'error': 'Two-factor authentication required'}
                    raise e
                
                auth.to_file(self.auth_file, encryption=False)
                
                # Create client
                self._client = audible.Client(auth=auth)
                self._auth_valid = True  # Mark auth as valid after successful login
                log.info('Audible authentication successful for country=%s', country_code)
                return {'success': True}
        except Exception as e:
            error_msg = str(e).lower()
            log.exception('Audible authentication failed')
            
            # Check if the error indicates 2FA is required (fallback check)
            if otp_code is None and ('otp' in error_msg or 'two' in error_msg or 'factor' in error_msg or '2fa' in error_msg):
                return {'success': False, 'requires_otp': True, 'error': 'Two-factor authentication required'}
            
            return {'success': False, 'error': str(e)}

    def logout(self):
        """Clear authentication and remove auth file."""
        try:
            with self._lock:
                self._client = None
                if os.path.exists(self.auth_file):
                    try:
                        os.remove(self.auth_file)
                    except Exception:
                        log.exception('Failed to remove auth file')
        except Exception:
            log.exception('Logout failed')

    def get_library(self, page: int = 1, num_results: int = 50) -> Dict[str, Any]:
        """Fetch user's Audible library.
        
        Args:
            page: Page number (1-indexed)
            num_results: Results per page
            
        Returns:
            dict with 'items': list of books and 'total': int or 'error': str
        """
        if not self.is_authenticated():
            return {'error': 'Not authenticated'}
        
        try:
            with self._lock:
                # Fetch library using audible API
                library_response = self._client.get(
                    'library',
                    num_results=num_results,
                    page=page,
                    response_groups='product_desc,media,relationships'
                )
                
                items = library_response.get('items', [])
                total = library_response.get('total_results', 0)
                
                # Normalize to simpler format
                books = []
                for item in items:
                    try:
                        book = {
                            'asin': item.get('asin'),
                            'title': item.get('title'),
                            'authors': [a.get('name') for a in item.get('authors', [])],
                            'narrators': [n.get('name') for n in item.get('narrators', [])],
                            'runtime_minutes': item.get('runtime_length_min', 0),
                            'release_date': item.get('release_date'),
                            'cover_url': item.get('product_images', {}).get('500'),
                            'is_downloaded': False,  # placeholder; check local cache
                        }
                        books.append(book)
                    except Exception:
                        log.exception('Failed to parse library item')
                        continue
                
                return {'items': books, 'total': total, 'page': page}
        except Exception as e:
            log.exception('Failed to fetch Audible library')
            return {'error': str(e)}

    def download_audiobook(self, asin: str, output_dir: str, quality: str = 'best') -> Dict[str, Any]:
        """Download an audiobook AAXC file.
        
        Args:
            asin: Audible ASIN identifier
            output_dir: Directory to save AAXC file
            quality: Quality setting ('best', 'high', 'normal')
            
        Returns:
            dict with 'success': bool, 'path': str (if success), or 'error': str
        """
        if not self.is_authenticated():
            return {'success': False, 'error': 'Not authenticated'}
        
        try:
            import asyncio
            import json
            from audible_cli.models import Library
            from audible_cli.downloader import Downloader, Status
            from pathlib import Path
            
            os.makedirs(output_dir, exist_ok=True)
            
            async def _download_async():
                # Create an async client from the auth with proper locale
                # The auth object contains the locale from when it was created
                auth = self._client.auth
                async with audible.AsyncClient(auth=auth, country_code=auth.locale.country_code) as async_client:
                    # Fetch library with the specific item
                    try:
                        library = await Library.from_api(
                            async_client
                        )
                    except Exception as e:
                        log.error('Failed to fetch library: %s', e)
                        return {'success': False, 'error': f'Failed to fetch library: {str(e)}'}
                    
                    # Find the item by ASIN
                    item = library.get_item_by_asin(asin)
                    if item is None:
                        return {'success': False, 'error': f'Book with ASIN {asin} not found in your library'}
                    
                    title = item.full_title or item.title or 'audiobook'
                    
                    # Get AAXC download URL using audible-cli's helper method
                    try:
                        url, codec, license_response = await item.get_aaxc_url(quality)
                    except Exception as e:
                        error_msg = str(e)
                        if '403' in error_msg or 'Forbidden' in error_msg:
                            return {'success': False, 'error': 'Access denied. This book may not be available for download or may require purchase.'}
                        log.exception('Failed to get AAXC URL for %s', asin)
                        return {'success': False, 'error': f'Failed to get download URL: {error_msg}'}
                    
                    # Determine file extension based on codec
                    if codec.lower() == "mpeg":
                        ext = "mp3"
                    else:
                        ext = "aaxc"
                    
                    # Sanitize title for filename
                    safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '_', '-')).strip()
                    if not safe_title:
                        safe_title = asin
                    
                    output_file = os.path.join(output_dir, f'{safe_title}-{codec}.{ext}')
                    voucher_file = os.path.join(output_dir, f'{safe_title}-{codec}.voucher')
                    
                    # Save voucher file (license response needed for decryption)
                    try:
                        with open(voucher_file, 'w') as f:
                            json.dump(license_response, f, indent=4)
                        log.info('Voucher file saved to %s', voucher_file)
                    except Exception as e:
                        log.warning('Failed to save voucher file: %s', e)
                    
                    # Download the audio file using audible-cli's Downloader
                    log.info('Downloading %s (%s) to %s', asin, codec, output_file)
                    
                    # Expected content types for AAXC/AAX files
                    expected_types = [
                        "audio/aax",
                        "audio/vnd.audible.aax",
                        "audio/mpeg",
                        "audio/x-m4a",
                        "audio/audible"
                    ]
                    
                    dl = Downloader(
                        source=url,
                        client=async_client.session,
                        expected_types=expected_types,
                        additional_headers=CLIENT_HEADERS
                    )
                    
                    downloaded = await dl.run(target=Path(output_file), force_reload=os.path.exists(output_file))
                    
                    if downloaded.status == Status.Success:
                        file_size = os.path.getsize(output_file)
                        log.info('Downloaded audiobook to %s (%d bytes, %.2f MB)', output_file, file_size, file_size / 1024 / 1024)
                        return {
                            'success': True,
                            'path': output_file,
                            'voucher_path': voucher_file,
                            'asin': asin,
                            'title': title,
                            'codec': codec,
                            'size_bytes': file_size
                        }
                    elif downloaded.status == Status.DownloadIndividualParts:
                        # This audiobook needs to be downloaded in parts
                        log.info('Item %s must be downloaded in individual parts', asin)
                        
                        # Get child items (audio parts)
                        child_items = await item.get_child_items()
                        if not child_items or len(child_items) == 0:
                            return {'success': False, 'error': 'No individual parts found for download'}
                        
                        log.info('Found %d parts to download', len(child_items))
                        
                        downloaded_parts = []
                        total_size = 0
                        
                        for idx, child in enumerate(child_items, 1):
                            # Only download AudioPart items
                            if child.content_delivery_type != "AudioPart":
                                log.debug('Skipping non-AudioPart item: %s', child.asin)
                                continue
                            
                            log.info('Downloading part %d/%d: %s', idx, len(child_items), child.asin)
                            
                            try:
                                # Get download URL for this part
                                part_url, part_codec, part_license = await child.get_aaxc_url(quality)
                                
                                # Determine extension
                                if part_codec.lower() == "mpeg":
                                    part_ext = "mp3"
                                else:
                                    part_ext = "aaxc"
                                
                                # Create filename for this part
                                part_filename = f'{safe_title}-{part_codec}-part{idx:03d}.{part_ext}'
                                part_output_file = os.path.join(output_dir, part_filename)
                                part_voucher_file = os.path.join(output_dir, f'{safe_title}-{part_codec}-part{idx:03d}.voucher')
                                
                                # Save part voucher
                                try:
                                    with open(part_voucher_file, 'w') as f:
                                        json.dump(part_license, f, indent=4)
                                    log.debug('Part %d voucher saved', idx)
                                except Exception as e:
                                    log.warning('Failed to save part %d voucher: %s', idx, e)
                                
                                # Download the part
                                part_dl = Downloader(
                                    source=part_url,
                                    client=async_client.session,
                                    expected_types=expected_types
                                )
                                
                                part_downloaded = await part_dl.run(target=Path(part_output_file), force_reload=True)
                                
                                if part_downloaded.status == Status.Success:
                                    part_size = os.path.getsize(part_output_file)
                                    total_size += part_size
                                    downloaded_parts.append({
                                        'part': idx,
                                        'path': part_output_file,
                                        'voucher_path': part_voucher_file,
                                        'size_bytes': part_size
                                    })
                                    log.info('Downloaded part %d: %s (%.2f MB)', idx, part_output_file, part_size / 1024 / 1024)
                                else:
                                    log.error('Failed to download part %d with status: %s', idx, part_downloaded.status)
                                    return {'success': False, 'error': f'Failed to download part {idx} with status: {part_downloaded.status}'}
                            
                            except Exception as e:
                                log.exception('Error downloading part %d', idx)
                                return {'success': False, 'error': f'Error downloading part {idx}: {str(e)}'}
                        
                        log.info('Downloaded all %d parts, total size: %.2f MB', len(downloaded_parts), total_size / 1024 / 1024)
                        return {
                            'success': True,
                            'multipart': True,
                            'parts': downloaded_parts,
                            'asin': asin,
                            'title': title,
                            'codec': codec,
                            'total_parts': len(downloaded_parts),
                            'total_size_bytes': total_size
                        }
                    else:
                        return {'success': False, 'error': f'Download failed with status: {downloaded.status}'}
            
            # Run the async download
            with self._lock:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # If there's already a running loop, create a new one in a thread
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            future = executor.submit(asyncio.run, _download_async())
                            return future.result()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                
                return loop.run_until_complete(_download_async())
        except Exception as e:
            log.exception('Failed to download audiobook %s', asin)
            return {'success': False, 'error': str(e)}

    def get_activation_bytes(self) -> Optional[str]:
        """Retrieve activation bytes needed for AAX/AAXC decryption.
        
        Returns:
            Hex string of activation bytes or None
        """
        if not self.is_authenticated():
            return None
        
        try:
            with self._lock:
                # Try to extract activation bytes from auth
                auth = self._client.auth
                if hasattr(auth, 'activation_bytes'):
                    return auth.activation_bytes
                # Alternative: compute from device credentials
                # This may require additional API calls depending on audible library version
                log.warning('Activation bytes not directly available; may need manual extraction')
                return None
        except Exception:
            log.exception('Failed to get activation bytes')
            return None
