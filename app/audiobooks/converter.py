"""
AAX/AAXC to M4B converter using AAXtoMP3_Python.

This module wraps the AAXtoMP3_Python tool to convert Audible audio files
to M4B format for use with standard media players.
"""
import os
import logging
import subprocess
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List
import json

log = logging.getLogger(__name__)

# Track conversion jobs
_conversion_jobs = {}
_conversion_lock = threading.Lock()


class ConversionJob:
    """Represents a single AAX/AAXC to M4B conversion job."""
    
    def __init__(self, job_id: str, input_path: str, output_dir: str):
        self.job_id = job_id
        self.input_path = input_path
        self.output_dir = output_dir
        self.status = 'pending'  # pending, running, completed, failed
        self.progress = 0
        self.output_file = None
        self.error = None
        self.thread = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'job_id': self.job_id,
            'input_path': self.input_path,
            'output_dir': self.output_dir,
            'status': self.status,
            'progress': self.progress,
            'output_file': self.output_file,
            'error': self.error,
        }


def get_aaxtomp3_path() -> Optional[str]:
    """Find the AAXtoMP3_Python script.
    
    Expected locations:
    - Same level as repo root: ../AAXtoMP3_Python/aaxtomp3.py
    - Vendored into app: app/vendor/AAXtoMP3_Python/aaxtomp3.py
    - In PATH as 'aaxtomp3' or 'aaxtomp3.py'
    
    Returns:
        Path to aaxtomp3.py or None if not found
    """
    # Check sibling directory (common for development)
    try:
        repo_root = Path(__file__).parent.parent.parent
        sibling = repo_root.parent / 'AAXtoMP3_Python' / 'aaxtomp3.py'
        if sibling.exists():
            return str(sibling)
    except Exception:
        pass
    
    # Check vendored location
    try:
        vendored = Path(__file__).parent.parent / 'vendor' / 'AAXtoMP3_Python' / 'aaxtomp3.py'
        if vendored.exists():
            return str(vendored)
    except Exception:
        pass
    
    # Check PATH
    import shutil
    for name in ['aaxtomp3', 'aaxtomp3.py']:
        found = shutil.which(name)
        if found:
            return found
    
    log.warning('AAXtoMP3_Python not found; conversion will fail')
    return None


def convert_aax_to_m4b(
    input_file: str,
    output_dir: str,
    activation_bytes: Optional[str] = None,
    job_id: Optional[str] = None
) -> Dict[str, Any]:
    """Convert AAX/AAXC file to M4B using AAXtoMP3_Python.
    
    Args:
        input_file: Path to AAX or AAXC file
        output_dir: Directory to save converted M4B
        activation_bytes: Hex string of activation bytes (required for AAX)
        job_id: Optional job ID for tracking async conversions
        
    Returns:
        dict with 'success': bool, 'output': str (if success), or 'error': str
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
        
        converter_path = get_aaxtomp3_path()
        if not converter_path:
            return {'success': False, 'error': 'AAXtoMP3_Python not found. Clone https://github.com/BubbaFatAss/AAXtoMP3_Python'}
        
        if not os.path.exists(input_file):
            return {'success': False, 'error': f'Input file not found: {input_file}'}
        
        # Build command
        # aaxtomp3.py usage: aaxtomp3.py [options] files...
        cmd = ['python', converter_path]
        
        # Force M4B output format with -e:m4b (extension:m4b)
        cmd.extend(['-e:m4b'])
        
        # Add activation bytes if provided (needed for AAX, optional for AAXC)
        if activation_bytes:
            cmd.extend(['--authcode', activation_bytes])
        
        # Set output directory
        cmd.extend(['--target_dir', output_dir])
        
        # Input file (positional argument)
        cmd.append(input_file)
        
        log.info('Running conversion: %s', ' '.join(cmd))
        
        # Run conversion
        if job_id:
            # Async conversion with job tracking
            with _conversion_lock:
                if job_id in _conversion_jobs:
                    job = _conversion_jobs[job_id]
                else:
                    job = ConversionJob(job_id, input_file, output_dir)
                    _conversion_jobs[job_id] = job
            
            def _run():
                try:
                    job.status = 'running'
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=7200  # 2 hour timeout
                    )
                    
                    if result.returncode == 0:
                        # Find output file (AAXtoMP3_Python names it based on title)
                        # Look for .m4b files in output_dir
                        m4b_files = list(Path(output_dir).glob('*.m4b'))
                        if m4b_files:
                            job.output_file = str(m4b_files[0])
                            job.status = 'completed'
                            job.progress = 100
                            log.info('Conversion completed: %s', job.output_file)
                            
                            # Clean up temporary files after successful conversion
                            try:
                                input_path = Path(input_file)
                                if input_path.exists():
                                    log.info('Removing temporary input file: %s', input_file)
                                    input_path.unlink()
                                
                                # Also remove associated .voucher file if it exists
                                voucher_path = input_path.with_suffix('.voucher')
                                if voucher_path.exists():
                                    log.info('Removing temporary voucher file: %s', voucher_path)
                                    voucher_path.unlink()
                            except Exception as cleanup_err:
                                log.warning('Failed to clean up temporary files: %s', cleanup_err)
                        else:
                            job.status = 'failed'
                            job.error = 'No M4B output found after conversion'
                    else:
                        job.status = 'failed'
                        job.error = f'Conversion failed: {result.stderr}'
                        log.error('Conversion failed: %s', result.stderr)
                except subprocess.TimeoutExpired:
                    job.status = 'failed'
                    job.error = 'Conversion timed out (> 2 hours)'
                except Exception as e:
                    job.status = 'failed'
                    job.error = str(e)
                    log.exception('Conversion failed')
            
            job.thread = threading.Thread(target=_run, daemon=True)
            job.thread.start()
            
            return {'success': True, 'job_id': job_id, 'status': 'running'}
        else:
            # Synchronous conversion
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=7200
            )
            
            if result.returncode == 0:
                # Find output
                m4b_files = list(Path(output_dir).glob('*.m4b'))
                if m4b_files:
                    output_file = str(m4b_files[0])
                    log.info('Conversion completed: %s', output_file)
                    
                    # Clean up temporary files after successful conversion
                    try:
                        input_path = Path(input_file)
                        if input_path.exists():
                            log.info('Removing temporary input file: %s', input_file)
                            input_path.unlink()
                        
                        # Also remove associated .voucher file if it exists
                        voucher_path = input_path.with_suffix('.voucher')
                        if voucher_path.exists():
                            log.info('Removing temporary voucher file: %s', voucher_path)
                            voucher_path.unlink()
                    except Exception as cleanup_err:
                        log.warning('Failed to clean up temporary files: %s', cleanup_err)
                    
                    return {'success': True, 'output': output_file}
                else:
                    return {'success': False, 'error': 'No M4B output found after conversion'}
            else:
                log.error('Conversion failed: %s', result.stderr)
                return {'success': False, 'error': result.stderr}
    
    except subprocess.TimeoutExpired:
        return {'success': False, 'error': 'Conversion timed out (> 2 hours)'}
    except Exception as e:
        log.exception('Conversion failed')
        return {'success': False, 'error': str(e)}


def get_job_status(job_id: str) -> Optional[Dict[str, Any]]:
    """Get status of a conversion job.
    
    Args:
        job_id: Job identifier
        
    Returns:
        Job dict or None if not found
    """
    with _conversion_lock:
        job = _conversion_jobs.get(job_id)
        if job:
            return job.to_dict()
        return None


def list_jobs() -> List[Dict[str, Any]]:
    """List all conversion jobs."""
    with _conversion_lock:
        return [job.to_dict() for job in _conversion_jobs.values()]


def cancel_job(job_id: str) -> bool:
    """Cancel a running conversion job.
    
    Args:
        job_id: Job identifier
        
    Returns:
        True if cancelled, False if not found or already finished
    """
    with _conversion_lock:
        job = _conversion_jobs.get(job_id)
        if job and job.status == 'running':
            # Note: subprocess doesn't support easy cancellation
            # Best effort: mark as failed
            job.status = 'failed'
            job.error = 'Cancelled by user'
            return True
        return False
