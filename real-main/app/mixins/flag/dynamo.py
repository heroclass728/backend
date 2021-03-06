import logging

import pendulum

from .exceptions import AlreadyFlagged, NotFlagged

logger = logging.getLogger()


class FlagDynamo:
    def __init__(self, item_type, dynamo_client):
        self.item_type = item_type
        self.client = dynamo_client

    def pk(self, item_id, user_id):
        return {
            'partitionKey': f'{self.item_type}/{item_id}',
            'sortKey': f'flag/{user_id}',
        }

    def get(self, item_id, user_id):
        return self.client.get_item(self.pk(item_id, user_id))

    def add(self, item_id, user_id, now=None):
        now = now or pendulum.now('utc')
        query_kwargs = {
            'Item': {
                **self.pk(item_id, user_id),
                'schemaVersion': 0,
                'gsiK1PartitionKey': f'flag/{user_id}',
                'gsiK1SortKey': self.item_type,
                'createdAt': now.to_iso8601_string(),
            },
        }
        try:
            return self.client.add_item(query_kwargs)
        except self.client.exceptions.ConditionalCheckFailedException as err:
            raise AlreadyFlagged(self.item_type, item_id, user_id) from err

    def delete(self, item_id, user_id):
        deleted = self.client.delete_item(self.pk(item_id, user_id))
        if not deleted:
            raise NotFlagged(self.item_type, item_id, user_id)

    def delete_all_for_item(self, item_id):
        generator = self.generate_by_item(item_id, pks_only=True)
        self.client.batch_delete_items(generator)

    def generate_by_item(self, item_id, pks_only=False):
        query_kwargs = {
            'KeyConditionExpression': 'partitionKey = :pk AND begins_with(sortKey, :sk_prefix)',
            'ExpressionAttributeValues': {':pk': f'{self.item_type}/{item_id}', ':sk_prefix': 'flag/'},
        }
        if pks_only:
            query_kwargs['ProjectionExpression'] = 'partitionKey, sortKey'
        return self.client.generate_all_query(query_kwargs)

    def generate_item_ids_by_user(self, user_id):
        query_kwargs = {
            'ProjectionExpression': 'partitionKey',
            'KeyConditionExpression': 'gsiK1PartitionKey = :pk AND gsiK1SortKey = :sk',
            'ExpressionAttributeValues': {':pk': f'flag/{user_id}', ':sk': self.item_type},
            'IndexName': 'GSI-K1',
        }
        prefix_len = len(self.item_type) + 1
        return (i['partitionKey'][prefix_len:] for i in self.client.generate_all_query(query_kwargs))
