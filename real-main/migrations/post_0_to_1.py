import os

import boto3

DYNAMO_TABLE = os.environ.get('DYNAMO_TABLE')
if not DYNAMO_TABLE:
    raise Exception("Must set env variable DYNAMO_TABLE to dynamo table name")

boto_table = boto3.resource('dynamodb').Table(DYNAMO_TABLE)


def generate_all_posts(version):
    "Return a generator of all items in the table that pass the filter"
    assert isinstance(version, int)
    scan_kwargs = {
        'FilterExpression': 'begins_with(partitionKey, :pk_prefix) and schemaVersion = :sv',
        'ExpressionAttributeValues': {':pk_prefix': 'post/', ':sv': version},
    }
    while True:
        paginated = boto_table.scan(**scan_kwargs)
        for item in paginated['Items']:
            yield item
        if 'LastEvaluatedKey' not in paginated:
            break
        scan_kwargs['ExclusiveStartKey'] = paginated['LastEvaluatedKey']


def update_post_from_0_to_1(like):
    kwargs = {
        'Key': {'partitionKey': like['partitionKey'], 'sortKey': like['sortKey']},
        'UpdateExpression': 'SET postedByUserId = postedBy.userId, schemaVersion = :one REMOVE postedBy',
        'ConditionExpression': 'attribute_exists(partitionKey) and schemaVersion = :zero',
        'ExpressionAttributeValues': {':zero': 0, ':one': 1},
    }
    print(f'Updating item: {kwargs} ... ')
    boto_table.update_item(**kwargs)
    print('Done.')


def main():
    for item in generate_all_posts(0):
        update_post_from_0_to_1(item)


main()
