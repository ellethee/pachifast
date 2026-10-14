# Upgrading

Read the changelog section of every version you cross, from the one you run to the one you install. The complete list is in the [changelog](../changelog.md). While the major version is 0, a minor release may break.

## After every upgrade

After any upgrade of the core, check whether the database needs a revision for the `auth` target:

```bash
uv run pachifast migrations revision "check" --target-name auth --autogenerate   # an empty revision means nothing to do
uv run pachifast migrations upgrade --target-name auth
```

Delete the revision when it is empty.

## How breaking changes are announced

A breaking change is listed in the changelog (under **Changed**, marked **Breaking**, or under **Removed**) and, when it needs an action from you, described in [Upgrade notes](#upgrade-notes) below. The promises behind this, and the deprecation policy, are in [Stability and support](stability.md).

## Pinning

While the major version is 0, pin the minor in your project so that a breaking release never arrives by surprise:

```text
pachifast>=0.15,<0.16
```

Move the pin only after reading the changelog of the new minor.

## Upgrade notes

No upgrade steps yet: 0.15 is the first public release. Future breaking changes will be listed here per version.
