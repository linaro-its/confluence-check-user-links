# Confluence Check User Links

This script scans a space on Confluence Server/Data Centre, looking for any use of the `@` macro. If found, the referenced user is retrieved and, if no longer valid, replaced with a plain-text version of the reference.

The reason for this script is because while Confluence retains some form of "memory" of ex-users, that memory is completely lost when migrating to Confluence Cloud. Where the `@` macro may say something like "Unknown User (their@email.addr)", that gets replaced with "Former User" and no email details.

## Setting up

Copy `config.sample.jsonc` to `config.jsonc`. Edit it as required, replacing `<your email address>`, `<your password>` and `<space key>` as appropriate.

NOTE! The credentials used must have access to all pages in the space otherwise the scan will be incomplete.

## Running the script

``` bash
pipenv install
pipenv run python check_user_links.py
```
