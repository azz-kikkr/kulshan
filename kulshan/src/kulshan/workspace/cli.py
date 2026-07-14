"""CLI commands for workspace management."""
from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from kulshan.redact import redact_account_id, redact_arn
from kulshan.workspace.config import read_workspace_config
from kulshan.workspace.errors import WorkspaceNotFoundError, WorkspaceConfigError
from kulshan.workspace.paths import get_workspace_path
from kulshan.workspace.resolution import (
    list_workspaces,
    resolve_workspace,
    set_active_workspace_name,
    get_active_workspace_name,
    workspace_exists,
)
from kulshan.workspace.validation import validate_workspace_name


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
