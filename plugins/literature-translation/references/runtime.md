# Local runtime

Run the CLI through the bundled source launcher:

```text
python <plugin-root>/scripts/littrans.py doctor
```

The first invocation creates a versioned virtual environment in the current user's cache and installs the plugin's declared Python dependencies. This may need package-index network access. Subsequent invocations reuse that environment. The launcher never installs into the system Python and never stores the environment inside the plugin or translation project.

When `littrans` is already installed as a console command, it is equivalent and may be used directly.
