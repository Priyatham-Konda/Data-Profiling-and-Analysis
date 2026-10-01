"""Salesforce, connected through the OAuth 2.0 client credentials flow.

The client's Salesforce administrator creates an app in their own org with
the client credentials flow enabled and a Run As user, and provides three
values: the org's address, the client ID and the client secret. The backend
exchanges them for an access token, downloads the selected objects with
SOQL, and then discards the secret and revokes the token. See API_CONTRACT.md
revision 5.

Modules:

    auth         the org's address, and exchanging credentials for a token
    client       the Salesforce REST, query and Bulk API calls used
    catalogue    which objects to offer in the picker
    extract      downloading one object to CSV
    connections  in-memory connections; the only place a secret is held
    source       turning a selection into an AssessmentPlan
    routes       the /salesforce endpoints
"""
