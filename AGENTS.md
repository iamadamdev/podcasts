# Repository working agreements

## Git identity

For every commit in this repository, use both author and committer identity:

- Name: `Adam`
- Email: `36013816+iamadamdev@users.noreply.github.com`

Explicitly set `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`, and
`GIT_COMMITTER_EMAIL` when committing; do not rely on global Git configuration or
inherited identity environment variables. Preserve the pinned identity in the
feed publisher when modifying automation.

## Git commit messages

Write a specific imperative subject. For non-trivial changes, include a body
explaining the important changes, why they were made, and relevant validation.
