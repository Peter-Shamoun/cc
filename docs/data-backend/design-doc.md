<!-- The user's own design doc (written 2026-10-23, pasted 2026-10-24), kept verbatim. The written companion to the canvas is README.md. -->

Claim Clarity backend data design: How it works


This data design is modeled after LabCorp 835/837 data integration, and makes some key assumptions.

First, it assumes that we can make these modifications to the prod bucket, potentially also test in the dev bucket, and that this is approved PHI data storage protocol. It also assumes that the same SFTP process that LabCorp uses can be used here, moving files from SFTP to AWS with a script from IT
---
The design:

 Any given customer, such as LabCorp, has their 835 and 837 (later also 277) files stored in SFTP. The SFTP files are updated daily with new data, and on a fixed daily schedule, we have a script that moves these files from SFTP to AWS prod bucket. 

We have some parent folder in the bucket, such as ClaimClarity (subject to change), and within that folder, there are subfolders for each customer. The general philosophy is that every customer will have its own subfolder, each with its own different encryption key and different IAM roles that provide access. This creates two layers of security for a user to access customer data, they would need the encryption key to the subfolder, and also the role to use that key

Within each customer's s3 subfolder, their folder structure is structured via a standard bronze, Silver, and Gold layer. The bronze layer is the raw edi files themselves. They live under a raw file, and each day has its own subfolder,  labeled for that day. The raw folder is importantly immutable, after files are added, they can not be changed, they are the source of truth. Some additional policies we propose adding are bucket policies that refuse any roles from touching the customer folders who don't have scoped permission, and cloudwatch to log events for transparency. 

Our silver layer is our parquet schema. This is a lossless 835/837p edi schema. The folder contains child folders per table, and each table has their daily parquet files added.

Our gold layer is our semantic layer, views built from the parquet schema that are the interaction layer for the agent's queries in 90% of query cases. We build these views, which are curated from their schema, so the agent does not need to write complex queries, or digest entire lossless parquet schemas in order to answer most user queries, which are likely to non-complex


To create these Silver and Gold layers, we rely on an EventBridge firing off an ECS on fargate compute. A key design choice here, is we want to scope our compute to only touch that customer's data, and terminate upon completion, sop that connection to the data is terminated, respecting the PHI boundary. 

Our event bridge listens for the _SUCCESS file to be added to our customer raw folders. Once it lands, we Start the task, scoped with the permissions to read and write to that customers folder ONLY.

Within the fargate task, we are going to read the raw file list, filter out what we actually need to parse and load into our parquet schema. We then use our lightweight native edi parser, all running locally, no data escaped the fargate task. We rwrite to some staging folder.

When we are done with all raw files, we promote the staging folder and add it to our silver tier parquet folder. We quarantine the failed files and flush the staging

Then, we rebuild our semantic layer views since our parquet data has changed. Our SQL for these views is version controlled on GitHub

We may need some job that handles parquet aggregation in the future, but for now, this is our data loading and storage design


App usage and auth:

Auth:
To use the app, a user has to go through 2 points of security, across 2 axis. They need to first auth with Microsoft entra id, and then cognito needs to issue them a valid token that maps to a customer. A user can be denied access at 2 stages; If their Microsoft account is not approved, based on our Entra ID filters, and if their entra id does not map to a token via cognito. 

The idea here is that we want a user to auth, we check which customer they map to (e.g. labcorp), and we give them some encrypted token. They give that token to the server whenever they make a request, the server gives that token to STS and sts issues some temp credentials. Those temp credentials are provided to the backend + duckdb server so we can query only that customers specific data. Creds are auto reissued after expiry, given the user still has the token. The token expires after some time, and the user must relog in. For now, we can assume chatlogs are not saved and these are anonymous sessions with no telemetry back to the server. Need to think about this
