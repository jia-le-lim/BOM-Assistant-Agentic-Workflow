"""Stage app source and private runtime settings beside an existing Supabase install.

Does not stop services or build images. Requires write access to DeploymentRoot.
Existing backend.docker.env is preserved on subsequent source updates.
"""
import argparse
import os
from pathlib import Path
import shutil
import stat
from urllib.parse import urlsplit, urlunsplit

from manage import read_env, write_private

HERE = Path(__file__).resolve().parent


def stage(repo, root):
    repo, root = repo.resolve(), root.resolve()
    stack = root / 'runtime/stack'
    if not (stack / '.env').is_file() or not (root / 'runtime/deployment.json').is_file():
        raise RuntimeError('Prepare the pinned local Supabase deployment first.')
    target = root / 'app-source'
    if target.exists():
        # Only this installer's explicitly marked, generated source copy can be replaced.
        if not (target / '.bom-app-source').is_file() or target.parent != root:
            raise RuntimeError('Refusing to replace an unrecognized source directory.')
        def remove_readonly(function, path, error):
            # OneDrive source directories can carry a read-only Windows attribute
            # into this generated copy. Clear it only inside the verified target.
            entry = Path(path)
            if (os.name != 'nt' or not isinstance(error, PermissionError)
                    or not entry.resolve().is_relative_to(target)
                    or not entry.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY):
                raise error
            entry.chmod(stat.S_IWRITE)
            function(path)

        shutil.rmtree(target, onexc=remove_readonly)
    target.mkdir(parents=True)
    (target / '.bom-app-source').write_text('Generated application build context. No runtime data.\n')
    for component, files, folders in (
        ('backend', ['Dockerfile', 'Dockerfile.dockerignore', 'requirements.txt'], ['app']),
        ('frontend', ['Dockerfile', '.dockerignore', 'package.json', 'package-lock.json',
                      'next.config.ts', 'next-env.d.ts', 'tsconfig.json', 'postcss.config.mjs'],
         ['src', 'public']),
    ):
        destination = target / component
        destination.mkdir()
        for name in files:
            source = repo / component / name
            if name == 'next-env.d.ts' and not source.exists():
                continue  # Next generates this during build.
            shutil.copy2(source, destination / name)
        for name in folders:
            shutil.copytree(repo / component / name, destination / name,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.env*'))
    shutil.copytree(repo / 'analysis/engine', target / 'analysis/engine',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.env*'))
    env_path = root / 'runtime/backend.docker.env'
    if not env_path.exists():
        original = read_env(repo / 'backend/.env')
        # Keep LLM and preference settings; database credentials come from the stack.
        values = {key: value for key, value in original.items()
                  if key.startswith(('LLM_', 'MEM0_', 'OPENAI_'))
                  or key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'BOM_ENGINE',
                             'BOM_WORKSPACE_READ_ALL_USERS')}
        values.pop('MEM0_DATABASE_URL', None)
        values.pop('MEM0_DIR', None)
        for key in ('LLM_BASE_URL', 'OPENAI_BASE_URL'):
            if not values.get(key):
                continue
            parsed = urlsplit(values[key])
            if parsed.hostname in ('localhost', '127.0.0.1', '::1'):
                if parsed.username or parsed.password:
                    raise RuntimeError('Local model URL must not contain embedded credentials.')
                host = 'host.docker.internal' + (f':{parsed.port}' if parsed.port else '')
                values[key] = urlunsplit(parsed._replace(netloc=host))
        excluded = [p.strip() for p in values.get('NO_PROXY', '').split(',') if p.strip()]
        values['NO_PROXY'] = ','.join(dict.fromkeys(excluded + [
            'localhost', '127.0.0.1', '::1', 'backend', 'frontend', 'api-gw',
            'rest', 'db', 'host.docker.internal']))
        write_private(env_path, '# Private Docker backend settings. Never commit or share in chat.\n'
                      + ''.join(f'{key}={value}\n' for key, value in values.items()))
    shutil.copy2(HERE / 'compose.app.yml', stack / 'compose.app.yml')
    for name in ('Compose.ps1', 'START_HERE.md', 'manage.py'):
        destination = root / name
        if (HERE / name).resolve() != destination:
            shutil.copy2(HERE / name, destination)
    print(f'Staged app source at {target}; private settings prepared. No services restarted.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=HERE.parents[1])
    parser.add_argument('--deployment-root', type=Path, default=Path('C:/ProgramData/BOM-Supabase'))
    args = parser.parse_args()
    stage(args.repo, args.deployment_root)
