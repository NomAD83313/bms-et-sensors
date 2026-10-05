"""Keep the dashboard API backed by the shared channel-state implementation."""

if __package__:
    from ..shared.redlab_channels import default_redlab_channels, load_redlab_channels, save_redlab_channels
else:
    from redlab_channels import default_redlab_channels, load_redlab_channels, save_redlab_channels

__all__ = ["default_redlab_channels", "load_redlab_channels", "save_redlab_channels"]
