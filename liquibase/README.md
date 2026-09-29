# Liquibase Setup Guide
# What is Liquibase?
Liquibase is a powerful database schema change management tool. In our setup, it is used to:
- Manage database tables directly from the Azure DevOps pipeline.
- Track and apply schema changes, including data insertions, in a controlled and versioned manner.

# Authentication & Security
We use a short-lived access token (valid for 10 minutes), generated on-the-fly by a Service Principal Name (SPN).(azure-pipeline/templates/liquibase/get-token.yml)
This ensures secure, temporary access to the database during pipeline execution.
# How to Set Up Liquibase in Your Pipeline

- Copy the liquibase/ directory into your feature branch.

- Update the run.sh Script:
    - update list of schema to reflect your target schemas. Schema where you need to manage tables
    example: 
        list_of_schemas=("app")
    REMEMBER: each schema should be reflected as seperate folder inside catalog folder where this run.sh is located. 

- Create a new directry(ies) under catalog directory:
    EXAMPLE: 
    please refer to current folder structure in application pipeline 
``` txt
liquibase
    ├── bia (this should be rename to catalog name where your schema(s) are located) 
        ├── tmp_data 
        (schema name where your tables should be managed by liquibase)
            ├── liquibase.properties 
            (please do not change it and place in every schema folder)
            ├── root.changelog.databricks.yaml 
            ( in this file you should include all .sql file that delivers infromation about tables to manage)
            ├── table_name.sql 
            ( ddl statements )
        ├──run.sh 
        ( script that is used to run liquibase on pipeline level please update list of schemas that we need to iterate through )
```                  


# Enable liquibase in Azure Pipeline
- In main.yml please add before deploy-dab.yml on each environemnt template to call liquibase: 
      - template: templates/deploy-liquibase.yml
        parameters:
            Environment: DEV
            BundleTarget: dev # Name of the target to use from databricks.yml
            CatalogName: bia_meta # Name of the catalog to use for tables deployment
  above sample is also included in app pipeline. 
REMEMBER: to call this with proper Environment and CatalogName parameter for each environment.



