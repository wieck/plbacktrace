from setuptools import setup

setup(
    name = 'plbacktrace',
    description = 'python module plbacktrace',
    version = '0.1.0',
    author = 'Jan Wieck',
    author_email = 'jan@wi3ck.info',
    url = None,
    license = 'PostgreSQL',
    packages = ['plbacktrace'],
    entry_points = {
        'console_scripts': ['plbacktrace = plbacktrace:main',
        ]
    },
    )
