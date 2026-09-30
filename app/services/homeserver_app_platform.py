from __future__ import annotations

from typing import Any

CONTRACT="vp3.app-platform.v1"
DEPLOYMENT_MODES=[
    "local",
    "private_remote",
    "hosted_subdomain",
    "custom_domain",
]

APP_ARCHETYPES=[
    {
        "key":"media_server",
        "name":"Media Server",
        "category":"Media",
        "description":"Index, organize, stream, and remotely serve HomeServer media libraries.",
        "runtime_profile":"long_running_service",
        "storage_profile":"large",
        "hosting":True,
        "suggested_permissions":["network.external"],
        "platform_integrations":["files","hosting","subdomains","agent","backup_recovery"],
    },
    {
        "key":"video_editor",
        "name":"Video Editor",
        "category":"Creative",
        "description":"Local browser-based video editing backed by HomeServer media and render workers.",
        "runtime_profile":"interactive_worker",
        "storage_profile":"large",
        "hosting":True,
        "suggested_permissions":["network.external"],
        "platform_integrations":["files","media","hosting","agent","jobs","backup_recovery"],
    },
    {
        "key":"photo_studio",
        "name":"Photo Studio",
        "category":"Creative",
        "description":"Local photo organization, editing, albums, and governed sharing.",
        "runtime_profile":"interactive",
        "storage_profile":"large",
        "hosting":True,
        "suggested_permissions":[],
        "platform_integrations":["files","hosting","agent","backup_recovery"],
    },
    {
        "key":"music_server",
        "name":"Music Server",
        "category":"Media",
        "description":"Local music library, playback, playlists, and remote streaming.",
        "runtime_profile":"long_running_service",
        "storage_profile":"large",
        "hosting":True,
        "suggested_permissions":["network.external"],
        "platform_integrations":["files","hosting","subdomains","agent","backup_recovery"],
    },
    {
        "key":"studio",
        "name":"Creator Studio",
        "category":"Creative",
        "description":"Reusable foundation for podcast, streaming, recording, and creator workflows.",
        "runtime_profile":"interactive_worker",
        "storage_profile":"large",
        "hosting":True,
        "suggested_permissions":["hardware.microphone","network.external"],
        "platform_integrations":["files","hosting","subdomains","agent","jobs","backup_recovery"],
    },
]


def capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "product_model":"optional_self_hosted_app",
        "core_homeserver_features_are_apps":False,
        "deployment_modes":list(DEPLOYMENT_MODES),
        "canonical_services":{
            "registry":"homeserver_apps",
            "packages":"homeserver_app_packages",
            "permissions":"homeserver_app_security",
            "storage":"homeserver_app_resources",
            "updates":"homeserver_app_releases",
            "recovery":"homeserver_app_data_lifecycle",
            "hosting":"hosting_cloud_control",
            "private_sharing":"homeserver_app_distribution",
            "agent":"homeserver_app_agent",
        },
        "duplicate_engines":False,
        "app_store":False,
        "archetypes":APP_ARCHETYPES,
    }
