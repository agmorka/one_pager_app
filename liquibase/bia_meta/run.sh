#!/bin/bash

# update the list when adding new schemas, order is important!
# add schema means new directory with a changelog.xml file
list_of_schemas=(
  "onepager_app"
)




format='+%Y-%m-%d %H:%M:%S'

########### input validation

usage() {
    echo "Usage: $0 --token <token> --env <environment> --catabv <catalog_name_abv> --wh <warehous_path> --host <databricks_host>"
    exit 1
}

if [ "$#" -ne 10 ]; then
    usage
fi

while [ "$1" != "" ]; do
    case $1 in
        --token ) shift
                  TOKEN=$1
                  ;;
        --env ) shift
                  ENV=$1
                  ;;
        --catabv ) shift
                  CATABV=$1
                  ;;
        --wh ) shift
                  WH=$1
                  ;;
        --host ) shift
                  HOST=$1
                  ;;
        * ) usage
            ;;
    esac
    shift
done

if [ -z "$TOKEN" ] || [ -z "$ENV" ] || [ -z "$CATABV" ] || [ -z "$WH" ]|| [ -z "$HOST" ]; then
    usage
fi

host=$HOST
warehouse=$WH

CATNAME=$ENV"_"$CATABV
CATSRC=$ENV"_bia"
export LIQUIBASE_COMMAND_PASSWORD="$TOKEN"
export JAVA_OPTS="-Dcatalog.name=$CATNAME -Dcatsrc.name=$CATSRC"

########### actual execution

for schema in "${list_of_schemas[@]}"; do
  echo "INFO $(date "$format"): Liquibase execution '$schema' started."
  pushd .
  cd $schema
  cmd='liquibase update --url jdbc:databricks://'$host':443/default;transportMode=http;ssl=1;AuthMech=3;httpPath='$warehouse';AuthMech=3;ConnCatalog='$CATNAME';ConnSchema='$schema';'
  $cmd
    if [ $? -ne 0 ]; then
    echo "ERROR $(date "$format"): Liquibase execution '$schema' failed."
    echo "ERROR $(date "$format"): Terminating the script."
    exit 2
  fi
  popd
done

echo "INFO $(date "$format"): Finished. All commands executed successfully."
exit 0