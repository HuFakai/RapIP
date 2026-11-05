# Standalone IPQuality database runner

Upstream: https://github.com/xykt/IPQuality/tree/b4f5c99e37e20411ef6311e90cf1ffb6dbb1c049
License: AGPL-3.0; included LICENSE applies to this standalone component.

Modification: added an opt-in library guard before upstream main. RapIP runner loads the original database functions and JSON serializer. It deliberately does not invoke upstream initialization, advertisements, report uploading, dependency installation, media/account access tests, SMTP or DNSBL scans. All curl requests use a temporary local SOCKS bridge to the explicitly selected exit; no supplier credentials are passed to the child. iso3166.json is pinned locally.

The wrapper and modified upstream source are provided here for users of this component. Reports are diagnostic references; they do not alter RapIP admission policy or call replacement APIs.
