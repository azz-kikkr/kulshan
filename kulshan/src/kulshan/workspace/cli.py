"""CLI commands for workspace management."""
from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from kulshan.redact import redact_account_id, redact_arn
from kulshan.workspace.config import (
    AwsConnection,
    WorkspaceAwsConfig,
    WorkspaceConfig,
    read_workspace_config,
    write_workspace_config,
)
from kulshan.workspace.errors import (
    WorkspaceConfigError,
    WorkspaceExistsError,
    WorkspaceNotFoundError,
    WorkspaceValidationError,
)
from kulshan.workspace.paths import get_workspace_path
from kulshan.workspace.resolution import (
    get_active_workspace_name,
    list_workspaces,
    resolve_workspace,
    set_active_workspace_name,
    workspace_exists,
)
from kulshan.workspace.validation import (
    validate_account_id,
    validate_connection_name,
    validate_profile_name,
    validate_workspace_name,
)


@click.group()
def workspace() -> None:
    """Manage workspaces for multi-payer isolation."""
    pass


@workspace.command("list")
def workspace_list() -> None:
    """List all workspaces."""
    console = Console()
    
    workspaces = list_workspaces()
    active = get_active_workspace_name()
    
    if not workspaces:
        console.print("[dim]No workspaces found.[/dim]")
        console.print("[dim]Run 'kulshan report' to create the default workspace.[/dim]")
        return
    
    table = Table(title="Workspaces", show_lines=False)
    table.add_column("Name", style="cyan")
    table.add_column("Display Name")
    table.add_column("Binding", justify="center")
    table.add_column("Active", justify="center")
    
    for name in workspaces:
        try:
            workspace_path = get_workspace_path(name)
            config = read_workspace_config(workspace_path)
            display = config.display_name or name
            binding = config.binding_mode
            is_active = "✓" if name == active else ""
            
            binding_style = "green" if binding == "bound" else "dim"
            table.add_row(
                name,
                display,
                f"[{binding_style}]{binding}[/{binding_style}]",
                is_active,
            )
        except (WorkspaceConfigError, WorkspaceNotFoundError):
            table.add_row(name, "[red]<invalid>[/red]", "?", "")
    
    console.print(table)


@workspace.command("show")
@click.argument("name", required=False)
@click.option("--show-pii", is_flag=True, help="Show full account IDs and ARNs.")
def workspace_show(name: str | None, show_pii: bool) -> None:
    """Show workspace details.
    
    If NAME is omitted, shows the active workspace.
    """
    console = Console()
    
    # Resolve workspace
    try:
        if name:
            validate_workspace_name(name, allow_default=True)
        ctx = resolve_workspace(name)
    except WorkspaceNotFoundError as e:
        console.print(f"[red]Workspace not found: {e.name}[/red]")
        raise SystemExit(1)
    except WorkspaceConfigError as e:
        console.print(f"[red]Invalid workspace configuration: {e.detail}[/red]")
        raise SystemExit(1)
    
    config = ctx.config
    active = get_active_workspace_name()
    
    console.print()
    console.print(f"[bold]Workspace:[/bold] {ctx.name}")
    if config.display_name and config.display_name != ctx.name:
        console.print(f"[bold]Display Name:[/bold] {config.display_name}")
    console.print(f"[bold]Path:[/bold] {ctx.path}")
    console.print(f"[bold]Binding:[/bold] {config.binding_mode}")
    
    if ctx.name == active:
        console.print("[bold]Active:[/bold] yes")
    
    if config.created_at:
        console.print(f"[bold]Created:[/bold] {config.created_at[:19]}")
    
    # Migration status
    if config.migration:
        console.print()
        console.print("[bold]Migration Status:[/bold]")
        console.print(f"  Main history: {config.migration.main_history}")
        console.print(f"  Security history: {config.migration.security_history}")
    
    # AWS configuration
    if config.aws:
        console.print()
        console.print("[bold]AWS Configuration:[/bold]")
        
        payer = config.aws.payer_account_id
        if not show_pii:
            payer = redact_account_id(payer)
        console.print(f"  Payer account: {payer}")
        console.print(f"  Default connection: {config.aws.default_connection}")
        
        if config.aws.connections:
            console.print()
            console.print("[bold]Connections:[/bold]")
            for conn in config.aws.connections:
                console.print(f"  [{conn.name}]")
                console.print(f"    Profile: {conn.profile}")
                
                session_account = conn.expected_session_account_id
                if not show_pii:
                    session_account = redact_account_id(session_account)
                console.print(f"    Expected session account: {session_account}")
                
                if conn.role_arn:
                    role = conn.role_arn
                    if not show_pii:
                        role = redact_arn(role)
                    console.print(f"    Role ARN: {role}")
    
    # Database paths
    console.print()
    console.print("[bold]Storage:[/bold]")
    console.print(f"  History DB: {ctx.history_db_path}")
    console.print(f"  Security DB: {ctx.security_history_db_path}")
    console.print()


@workspace.command("use")
@click.argument("name")
def workspace_use(name: str) -> None:
    """Set the active workspace.
    
    The active workspace is used when --workspace is not specified.
    """
    console = Console()
    
    try:
        validate_workspace_name(name, allow_default=True)
    except Exception as e:
        console.print(f"[red]Invalid workspace name: {e}[/red]")
        raise SystemExit(1)
    
    if not workspace_exists(name):
        console.print(f"[red]Workspace not found: {name}[/red]")
        raise SystemExit(1)
    
    set_active_workspace_name(name)
    console.print(f"Active workspace set to: [cyan]{name}[/cyan]")


# ---------------------------------------------------------------------------
# workspace create
# ---------------------------------------------------------------------------


@workspace.command("create")
@click.argument("name")
@click.option("--profile", required=True, help="AWS CLI profile name for the initial connection.")
@click.option("--payer-account", required=True, help="Payer/management account ID (12 digits).")
@click.option("--display-name", default=None, help="Human-readable workspace display name.")
@click.option("--connection-name", default=None, help="Name for the initial connection (default: profile name).")
@click.option("--credential-account", default=None, help="Assert the STS account ID (12 digits).")
@click.option("--role-arn", default=None, help="IAM role ARN to assume after profile authentication.")
def workspace_create(
    name: str,
    profile: str,
    payer_account: str,
    display_name: str | None,
    connection_name: str | None,
    credential_account: str | None,
    role_arn: str | None,
) -> None:
    """Create a new bound workspace with STS-verified credentials.

    \b
    Example:
      kulshan workspace create customer-a \\
        --profile customer-a-finops \\
        --payer-account 999999999999
    """
    from datetime import datetime, timezone

    from kulshan.workspace.sts import StsVerificationError, create_verified_session

    console = Console()

    # 1. Validate workspace name
    try:
        validate_workspace_name(name)
    except WorkspaceValidationError as e:
        console.print(f"[red]Invalid workspace name: {e}[/red]")
        raise SystemExit(1)

    # 2. Reject 'default'
    if name == "default":
        console.print("[red]Cannot create a workspace named 'default'. It is reserved.[/red]")
        raise SystemExit(1)

    # 3. Reject existing
    if workspace_exists(name):
        console.print(f"[red]Workspace '{name}' already exists.[/red]")
        raise SystemExit(1)

    # 4. Validate account IDs
    try:
        validate_account_id(payer_account, "payer-account")
    except WorkspaceValidationError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    if credential_account:
        try:
            validate_account_id(credential_account, "credential-account")
        except WorkspaceValidationError as e:
            console.print(f"[red]{e}[/red]")
            raise SystemExit(1)

    # 5. Validate connection and profile names
    conn_name = connection_name or profile.split("/")[-1].split("\\")[-1]
    try:
        validate_connection_name(conn_name)
        validate_profile_name(profile)
    except WorkspaceValidationError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    # 6-10. STS verification (before creating any directory)
    console.print(f"  Verifying credentials for profile [cyan]{profile}[/cyan]...")
    try:
        sts_result = create_verified_session(
            profile=profile,
            role_arn=role_arn,
            credential_account=credential_account,
        )
    except StsVerificationError as e:
        console.print(f"[red]Credential verification failed: {e}[/red]")
        raise SystemExit(1)

    console.print(
        f"  [green]✓[/green] Verified: account "
        f"{redact_account_id(sts_result.account_id)}"
    )

    # 11. Build complete configuration in memory
    connection = AwsConnection(
        name=conn_name,
        profile=profile,
        expected_session_account_id=sts_result.account_id,
        role_arn=role_arn,
    )

    aws_config = WorkspaceAwsConfig(
        payer_account_id=payer_account,
        default_connection=conn_name,
        connections=[connection],
    )

    config = WorkspaceConfig(
        name=name,
        display_name=display_name or name,
        created_at=datetime.now(timezone.utc).isoformat(),
        binding_mode="bound",
        aws=aws_config,
    )

    # 12. Write atomically — only after STS verification succeeds
    workspace_path = get_workspace_path(name)
    workspace_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        write_workspace_config(workspace_path, config)
    except Exception as e:
        # Clean up empty directory on write failure
        try:
            workspace_path.rmdir()
        except OSError:
            pass
        console.print(f"[red]Failed to write workspace configuration: {e}[/red]")
        raise SystemExit(1)

    console.print(f"  [green]✓[/green] Workspace [cyan]{name}[/cyan] created.")
    console.print()


# ---------------------------------------------------------------------------
# workspace connection subgroup
# ---------------------------------------------------------------------------


@workspace.group("connection")
def workspace_connection() -> None:
    """Manage AWS connections within a workspace."""
    pass


@workspace_connection.command("add")
@click.argument("workspace_name")
@click.option("--name", "conn_name", required=True, help="Connection name.")
@click.option("--profile", required=True, help="AWS CLI profile name.")
@click.option("--role-arn", default=None, help="IAM role ARN to assume.")
@click.option("--credential-account", default=None, help="Assert the STS account ID (12 digits).")
def connection_add(
    workspace_name: str,
    conn_name: str,
    profile: str,
    role_arn: str | None,
    credential_account: str | None,
) -> None:
    """Add a new AWS connection to an existing bound workspace.

    \b
    Example:
      kulshan workspace connection add customer-a \\
        --name audit \\
        --profile customer-a-audit
    """
    from kulshan.workspace.sts import StsVerificationError, create_verified_session

    console = Console()

    # Validate inputs
    try:
        validate_workspace_name(workspace_name, allow_default=True)
        validate_connection_name(conn_name)
        validate_profile_name(profile)
    except WorkspaceValidationError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    if credential_account:
        try:
            validate_account_id(credential_account, "credential-account")
        except WorkspaceValidationError as e:
            console.print(f"[red]{e}[/red]")
            raise SystemExit(1)

    # Load workspace
    ws_path = get_workspace_path(workspace_name)
    if not workspace_exists(workspace_name):
        console.print(f"[red]Workspace not found: {workspace_name}[/red]")
        raise SystemExit(1)

    try:
        config = read_workspace_config(ws_path)
    except WorkspaceConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    # Must be bound
    if config.binding_mode != "bound" or config.aws is None:
        console.print(
            f"[red]Workspace '{workspace_name}' is unbound. "
            "Cannot add connections to the default unbound workspace.[/red]"
        )
        raise SystemExit(1)

    # Duplicate connection name
    if config.aws.get_connection(conn_name):
        console.print(f"[red]Connection '{conn_name}' already exists in workspace '{workspace_name}'.[/red]")
        raise SystemExit(1)

    # Duplicate equivalent (same profile + same role)
    for existing in config.aws.connections:
        if existing.profile == profile and existing.role_arn == role_arn:
            console.print(
                f"[red]Equivalent connection already exists: '{existing.name}' "
                f"(same profile '{profile}'"
                f"{' and role' if role_arn else ''}).[/red]"
            )
            raise SystemExit(1)

    # STS verification
    console.print(f"  Verifying credentials for profile [cyan]{profile}[/cyan]...")
    try:
        sts_result = create_verified_session(
            profile=profile,
            role_arn=role_arn,
            credential_account=credential_account,
        )
    except StsVerificationError as e:
        console.print(f"[red]Credential verification failed: {e}[/red]")
        raise SystemExit(1)

    console.print(
        f"  [green]✓[/green] Verified: account "
        f"{redact_account_id(sts_result.account_id)}"
    )

    # Add connection
    new_connection = AwsConnection(
        name=conn_name,
        profile=profile,
        expected_session_account_id=sts_result.account_id,
        role_arn=role_arn,
    )
    config.aws.connections.append(new_connection)

    # Write atomically
    try:
        write_workspace_config(ws_path, config)
    except Exception as e:
        # Revert in-memory change
        config.aws.connections.remove(new_connection)
        console.print(f"[red]Failed to write configuration: {e}[/red]")
        raise SystemExit(1)

    console.print(f"  [green]✓[/green] Connection [cyan]{conn_name}[/cyan] added.")
    console.print()


@workspace_connection.command("remove")
@click.argument("workspace_name")
@click.argument("connection_name")
def connection_remove(workspace_name: str, connection_name: str) -> None:
    """Remove an AWS connection from a workspace.

    Cannot remove the last connection or the default connection
    (set another default first).

    \b
    Example:
      kulshan workspace connection remove customer-a audit
    """
    console = Console()

    # Validate
    try:
        validate_workspace_name(workspace_name, allow_default=True)
    except WorkspaceValidationError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    # Load workspace
    ws_path = get_workspace_path(workspace_name)
    if not workspace_exists(workspace_name):
        console.print(f"[red]Workspace not found: {workspace_name}[/red]")
        raise SystemExit(1)

    try:
        config = read_workspace_config(ws_path)
    except WorkspaceConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    if config.binding_mode != "bound" or config.aws is None:
        console.print(
            f"[red]Workspace '{workspace_name}' is unbound. "
            "Cannot manage connections on the default unbound workspace.[/red]"
        )
        raise SystemExit(1)

    # Connection must exist
    target = config.aws.get_connection(connection_name)
    if target is None:
        console.print(
            f"[red]Connection '{connection_name}' not found in "
            f"workspace '{workspace_name}'.[/red]"
        )
        raise SystemExit(1)

    # Cannot remove the last connection
    if len(config.aws.connections) <= 1:
        console.print(
            f"[red]Cannot remove the last connection from a bound workspace.[/red]"
        )
        raise SystemExit(1)

    # Cannot remove the default connection
    if connection_name == config.aws.default_connection:
        others = [c.name for c in config.aws.connections if c.name != connection_name]
        console.print(
            f"[red]Cannot remove default connection \"{connection_name}\".[/red]"
        )
        console.print()
        console.print("Set another default first:")
        console.print()
        for other in others:
            console.print(f"  kulshan workspace default-connection {workspace_name} {other}")
        console.print()
        raise SystemExit(1)

    # Remove and write
    config.aws.connections = [c for c in config.aws.connections if c.name != connection_name]

    try:
        write_workspace_config(ws_path, config)
    except Exception as e:
        console.print(f"[red]Failed to write configuration: {e}[/red]")
        raise SystemExit(1)

    console.print(f"  [green]✓[/green] Connection [cyan]{connection_name}[/cyan] removed.")
    console.print()


# ---------------------------------------------------------------------------
# workspace default-connection
# ---------------------------------------------------------------------------


@workspace.command("default-connection")
@click.argument("workspace_name")
@click.argument("connection_name")
def workspace_default_connection(workspace_name: str, connection_name: str) -> None:
    """Set the default connection for a workspace. No AWS call required.

    \b
    Example:
      kulshan workspace default-connection customer-a audit
    """
    console = Console()

    # Validate
    try:
        validate_workspace_name(workspace_name, allow_default=True)
        validate_connection_name(connection_name)
    except WorkspaceValidationError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    # Load workspace
    ws_path = get_workspace_path(workspace_name)
    if not workspace_exists(workspace_name):
        console.print(f"[red]Workspace not found: {workspace_name}[/red]")
        raise SystemExit(1)

    try:
        config = read_workspace_config(ws_path)
    except WorkspaceConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    if config.binding_mode != "bound" or config.aws is None:
        console.print(
            f"[red]Workspace '{workspace_name}' is unbound. "
            "Cannot set default connection on the unbound workspace.[/red]"
        )
        raise SystemExit(1)

    # Connection must exist
    if not config.aws.get_connection(connection_name):
        available = [c.name for c in config.aws.connections]
        console.print(
            f"[red]Connection '{connection_name}' not found. "
            f"Available: {', '.join(available)}[/red]"
        )
        raise SystemExit(1)

    # Update default
    old_default = config.aws.default_connection
    config.aws.default_connection = connection_name

    try:
        write_workspace_config(ws_path, config)
    except Exception as e:
        config.aws.default_connection = old_default
        console.print(f"[red]Failed to write configuration: {e}[/red]")
        raise SystemExit(1)

    console.print(
        f"  [green]✓[/green] Default connection for [cyan]{workspace_name}[/cyan] "
        f"set to [cyan]{connection_name}[/cyan]."
    )
    console.print()


# ---------------------------------------------------------------------------
# workspace rename
# ---------------------------------------------------------------------------


@workspace.command("rename")
@click.argument("name")
@click.argument("new_display_name")
def workspace_rename(name: str, new_display_name: str) -> None:
    """Rename a workspace's display name.

    Changes only the display name. The internal ID and database path
    remain stable.

    \b
    Example:
      kulshan workspace rename ws_7f3a842c "Acme Corporation"
      kulshan workspace rename acme-finops-cedar "Acme Corporation"
    """
    from kulshan.workspace.registry import find_entry_by_workspace_dir, update_display_name

    console = Console()

    # Validate workspace name
    try:
        validate_workspace_name(name, allow_default=True)
    except WorkspaceValidationError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    # Validate new display name (basic sanity)
    if not new_display_name or not new_display_name.strip():
        console.print("[red]Display name cannot be empty.[/red]")
        raise SystemExit(1)
    if len(new_display_name) > 128:
        console.print("[red]Display name too long (max 128 chars).[/red]")
        raise SystemExit(1)

    # Try to find workspace by name or by display name via registry
    ws_path = get_workspace_path(name)
    if not workspace_exists(name):
        # Try looking up in registry by display name
        from kulshan.workspace.registry import list_registry_entries

        entries = list_registry_entries()
        matches = [e for e in entries if e.display_name == name]
        if len(matches) == 1:
            ws_path = get_workspace_path(matches[0].workspace_dir)
            name = matches[0].workspace_dir
        elif len(matches) > 1:
            console.print(
                f"[red]Ambiguous: multiple workspaces with display name '{name}'. "
                f"Use the workspace directory name instead.[/red]"
            )
            raise SystemExit(1)
        else:
            console.print(f"[red]Workspace not found: {name}[/red]")
            raise SystemExit(1)

    # Load and update config
    try:
        config = read_workspace_config(ws_path)
    except WorkspaceConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    old_display = config.display_name or name
    config.display_name = new_display_name.strip()

    try:
        write_workspace_config(ws_path, config)
    except Exception as e:
        console.print(f"[red]Failed to write configuration: {e}[/red]")
        raise SystemExit(1)

    # Also update registry if this is an auto-onboarded workspace
    registry_entry = find_entry_by_workspace_dir(name)
    if registry_entry:
        update_display_name(
            profile=registry_entry.profile,
            role_arn=registry_entry.role_arn,
            account_id=registry_entry.account_id,
            new_display_name=new_display_name.strip(),
        )

    console.print(
        f"  [green]✓[/green] Renamed [dim]{old_display}[/dim] → "
        f"[cyan]{new_display_name.strip()}[/cyan]"
    )
    console.print()
