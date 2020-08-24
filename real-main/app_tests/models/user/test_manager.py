import logging
import re
import uuid
from decimal import Decimal
from unittest import mock

import pendulum
import pytest

from app.models.user.exceptions import UserAlreadyExists, UserValidationException
from app.utils import GqlNotificationType


@pytest.fixture
def cognito_only_user(user_manager, cognito_client):
    user_id, username = str(uuid.uuid4()), str(uuid.uuid4())[:8]
    cognito_client.create_verified_user_pool_entry(user_id, username, f'{username}@real.app')
    yield user_manager.create_cognito_only_user(user_id, username)


user1 = cognito_only_user
user2 = cognito_only_user
user3 = cognito_only_user


@pytest.fixture
def real_user(user_manager, cognito_client):
    user_id = str(uuid.uuid4())
    cognito_client.create_verified_user_pool_entry(user_id, 'real', 'real-test@real.app')
    yield user_manager.create_cognito_only_user(user_id, 'real')


def test_get_user_that_doesnt_exist(user_manager):
    resp = user_manager.get_user('nope-not-there')
    assert resp is None


def test_get_user_by_username(user_manager, user1):
    # check a user that doesn't exist
    user = user_manager.get_user_by_username('nope_not_there')
    assert user is None

    # check a user that exists
    user = user_manager.get_user_by_username(user1.username)
    assert user.id == user1.id


def test_create_cognito_user(user_manager, cognito_client):
    user_id = 'my-user-id'
    username = 'myusername'
    full_name = 'my-full-name'
    email = f'{username}@real.app'
    date_of_birth = '2020-08-20'
    gender = 'man'

    # check the user doesn't already exist
    user = user_manager.get_user(user_id)
    assert user is None

    # frontend does this part out-of-band
    cognito_client.create_verified_user_pool_entry(user_id, username, email)

    # create the user
    user = user_manager.create_cognito_only_user(user_id, username, full_name=full_name)
    assert user.id == user_id
    assert user.item['userId'] == user_id
    assert user.item['username'] == username
    assert user.item['fullName'] == full_name
    assert user.item['email'] == email
    assert 'phoneNumber' not in user.item
    assert user.item['date_of_birth'] == date_of_birth
    assert user.item['gender'] == gender

    # double check user got into db
    user = user_manager.get_user(user_id)
    assert user.id == user_id
    assert user.item['userId'] == user_id
    assert user.item['username'] == username
    assert user.item['fullName'] == full_name
    assert user.item['email'] == email
    assert 'phoneNumber' not in user.item
    assert user.item['date_of_birth'] == date_of_birth
    assert user.item['gender'] == gender

    # check cognito was set correctly
    assert user.cognito_client.get_user_attributes(user.id)['preferred_username'] == username


def test_create_cognito_user_aleady_exists(user_manager, cognito_client):
    user_id = 'my-user-id'
    username = 'orgusername'
    full_name = 'my-full-name'

    # create the user in the userpool (frontend does this in live system)
    cognito_client.create_verified_user_pool_entry(user_id, username, f'{username}@real.app')

    # create the user
    user = user_manager.create_cognito_only_user(user_id, username, full_name=full_name)
    assert user.id == user_id
    assert user.username == username

    # check their cognito username is as expected
    assert user.cognito_client.get_user_attributes(user.id)['preferred_username'] == username

    # try to create the user again, this time with a diff username
    with pytest.raises(UserAlreadyExists):
        user_manager.create_cognito_only_user(user_id, 'diffusername')

    # verify that did not affect either dynamo, cognito or pinpoint
    user = user_manager.get_user(user_id)
    assert user.username == username
    assert user.cognito_client.get_user_attributes(user.id)['preferred_username'] == username


def test_create_cognito_user_with_email_and_phone(user_manager, cognito_client):
    user_id = 'my-user-id'
    username = 'therealuser'
    full_name = 'my-full-name'
    email = 'great@best.com'
    phone = '+123'

    # frontend does this part out-of-band: creates the user in cognito with verified email and phone
    cognito_client.user_pool_client.admin_create_user(
        UserPoolId=cognito_client.user_pool_id,
        Username=user_id,
        UserAttributes=[
            {'Name': 'email', 'Value': email},
            {'Name': 'email_verified', 'Value': 'true'},
            {'Name': 'phone_number', 'Value': phone},
            {'Name': 'phone_number_verified', 'Value': 'true'},
        ],
    )

    # check the user doesn't already exist
    user = user_manager.get_user(user_id)
    assert user is None

    # create the user
    user = user_manager.create_cognito_only_user(user_id, username, full_name=full_name)
    assert user.id == user_id
    assert user.item['userId'] == user_id
    assert user.item['username'] == username
    assert user.item['fullName'] == full_name
    assert user.item['email'] == email
    assert user.item['phoneNumber'] == phone

    # check cognito attrs are as expected
    cognito_attrs = user.cognito_client.get_user_attributes(user.id)
    assert cognito_attrs['preferred_username'] == username
    assert cognito_attrs['email'] == email
    assert cognito_attrs['email_verified'] == 'true'
    assert cognito_attrs['phone_number'] == phone
    assert cognito_attrs['phone_number_verified'] == 'true'


def test_create_cognito_user_with_non_verified_email_and_phone(user_manager, cognito_client):
    user_id = 'my-user-id'
    username = 'therealuser'
    full_name = 'my-full-name'
    email = 'great@best.com'
    phone = '+123'

    # frontend does this part out-of-band: creates the user in cognito with unverified email and phone
    cognito_client.user_pool_client.admin_create_user(
        UserPoolId=cognito_client.user_pool_id,
        Username=user_id,
        UserAttributes=[
            {'Name': 'email', 'Value': email},
            {'Name': 'email_verified', 'Value': 'false'},
            {'Name': 'phone_number', 'Value': phone},
            {'Name': 'phone_number_verified', 'Value': 'false'},
        ],
    )

    # check the user doesn't already exist
    user = user_manager.get_user(user_id)
    assert user is None

    # check can't create the user
    with pytest.raises(UserValidationException):
        user_manager.create_cognito_only_user(user_id, username, full_name=full_name)


def test_create_cognito_only_user_invalid_username(user_manager):
    user_id = 'my-user-id'
    invalid_username = '-'
    full_name = 'my-full-name'

    with pytest.raises(UserValidationException):
        user_manager.create_cognito_only_user(user_id, invalid_username, full_name=full_name)


def test_create_cognito_only_user_username_taken(user_manager, cognito_only_user, cognito_client):
    user_id = 'uid'
    username_1 = cognito_only_user.username.upper()
    username_2 = cognito_only_user.username.lower()

    # frontend does this part out-of-band: creates the user in cognito, no preferred_username
    cognito_client.user_pool_client.admin_create_user(
        UserPoolId=cognito_client.user_pool_id, Username=user_id,
    )

    # moto doesn't seem to honor the 'make preferred usernames unique' setting (using it as an alias)
    # so mock it's response like to simulate that it does
    exception = user_manager.cognito_client.user_pool_client.exceptions.AliasExistsException({}, None)
    user_manager.cognito_client.set_user_attributes = mock.Mock(side_effect=exception)

    with pytest.raises(UserValidationException):
        user_manager.create_cognito_only_user(user_id, username_1)

    with pytest.raises(UserValidationException):
        user_manager.create_cognito_only_user(user_id, username_2)


def test_create_cognito_only_user_username_released_if_user_not_found_in_user_pool(user_manager, cognito_client):
    # two users, one username, cognito only has a user set up for one of them
    user_id_1 = 'my-user-id-1'
    user_id_2 = 'my-user-id-2'
    username = 'myUsername'
    cognito_client.user_pool_client.admin_create_user(
        UserPoolId=cognito_client.user_pool_id,
        Username=user_id_2,
        MessageAction='SUPPRESS',
        UserAttributes=[{'Name': 'email', 'Value': 'test@real.app'}, {'Name': 'email_verified', 'Value': 'true'}],
    )

    # create the first user that doesn't exist in the user pool, should fail
    with pytest.raises(UserValidationException):
        user_manager.create_cognito_only_user(user_id_1, username)

    # should be able to now use that same username with the other user
    user = user_manager.create_cognito_only_user(user_id_2, username)
    assert user.username == username
    assert cognito_client.get_user_attributes(user.id)['preferred_username'] == username.lower()


def test_create_cognito_only_user_follow_real_user_doesnt_exist(user_manager, cognito_client):
    # create a user, verify no followeds
    user_id, username = str(uuid.uuid4()), str(uuid.uuid4())[:8]
    cognito_client.create_verified_user_pool_entry(user_id, username, f'{username}@real.app')
    user = user_manager.create_cognito_only_user(user_id, username)
    assert list(user.follower_manager.dynamo.generate_followed_items(user.id)) == []


def test_follow_real_user_exists(user_manager, user1, follower_manager, real_user):
    # verify no followers (ensures user1 fixture generated before real_user)
    assert list(follower_manager.dynamo.generate_followed_items(user1.id)) == []

    # follow that real user
    user_manager.follow_real_user(user1)
    followeds = list(follower_manager.dynamo.generate_followed_items(user1.id))
    assert len(followeds) == 1
    assert followeds[0]['followedUserId'] == real_user.id


def test_follow_real_user_doesnt_exist(user_manager, user1, follower_manager):
    assert list(follower_manager.dynamo.generate_followed_items(user1.id)) == []
    user_manager.follow_real_user(user1)
    assert list(follower_manager.dynamo.generate_followed_items(user1.id)) == []


def test_create_cognito_only_user_follow_real_user_if_exists(user_manager, cognito_client, real_user):
    # create a user, verify follows real user
    user_id, username = str(uuid.uuid4()), str(uuid.uuid4())[:8]
    cognito_client.create_verified_user_pool_entry(user_id, username, f'{username}@real.app')
    user = user_manager.create_cognito_only_user(user_id, username)
    followeds = list(user.follower_manager.dynamo.generate_followed_items(user.id))
    assert len(followeds) == 1
    assert followeds[0]['followedUserId'] == real_user.id


@pytest.mark.parametrize('provider', ['apple', 'facebook', 'google'])
def test_create_federated_user_success(user_manager, real_user, provider):
    provider_token = 'fb-google-or-apple-token'
    cognito_token = 'cog-token'
    user_id = 'my-user-id'
    username = 'my_username'
    full_name = 'my-full-name'
    email = 'My@email.com'

    # set up our mocks to behave correctly
    user_manager.clients[provider].configure_mock(**{'get_verified_email.return_value': email})
    user_manager.cognito_client.create_verified_user_pool_entry = mock.Mock()
    user_manager.cognito_client.get_user_pool_id_token = mock.Mock(return_value=cognito_token)
    user_manager.cognito_client.link_identity_pool_entries = mock.Mock()

    # create the user, check it is as expected
    user = user_manager.create_federated_user(provider, user_id, username, provider_token, full_name=full_name)
    assert user.id == user_id
    assert user.item['username'] == username
    assert user.item['fullName'] == full_name
    assert user.item['email'] == email.lower()

    # check mocks called as expected
    assert user_manager.clients[provider].mock_calls == [mock.call.get_verified_email(provider_token)]
    assert user_manager.cognito_client.create_verified_user_pool_entry.mock_calls == [
        mock.call(user_id, username, email.lower()),
    ]
    assert user_manager.cognito_client.get_user_pool_id_token.mock_calls == [mock.call(user_id)]
    call_kwargs = {
        'cognito_token': cognito_token,
        provider + '_token': provider_token,
    }
    assert user_manager.cognito_client.link_identity_pool_entries.mock_calls == [
        mock.call(user_id, **call_kwargs)
    ]

    # check we are following the real user
    followeds = list(user.follower_manager.dynamo.generate_followed_items(user.id))
    assert len(followeds) == 1
    assert followeds[0]['followedUserId'] == real_user.id


@pytest.mark.parametrize('provider', ['apple', 'facebook', 'google'])
def test_create_federated_user_user_id_taken(user_manager, provider):
    # configure cognito to respond as if user_id is already taken
    user_id, username = str(uuid.uuid4()), str(uuid.uuid4())[:8]
    exception = user_manager.cognito_client.user_pool_client.exceptions.UsernameExistsException(
        {'Error': {'Code': '<code>', 'Message': 'User account already exists.'}}, '<operation name>'
    )
    user_manager.cognito_client.user_pool_client.admin_create_user = mock.Mock(side_effect=exception)
    with pytest.raises(UserValidationException, match=f'An account for userId `{user_id}` already exists'):
        user_manager.create_federated_user(provider, user_id, username, 'provider-token')


@pytest.mark.parametrize('provider', ['apple', 'facebook', 'google'])
def test_create_federated_user_username_taken(user_manager, provider):
    # configure cognito to respond as if username is already taken
    user_id, username = str(uuid.uuid4()), str(uuid.uuid4())[:8]
    exception = user_manager.cognito_client.user_pool_client.exceptions.UsernameExistsException(
        {'Error': {'Code': '<code>', 'Message': 'Already found an entry for the provided username.'}},
        '<operation name>',
    )
    user_manager.cognito_client.user_pool_client.admin_create_user = mock.Mock(side_effect=exception)
    with pytest.raises(UserValidationException, match=f'Username `{username}` already taken'):
        user_manager.create_federated_user(provider, user_id, username, 'provider-token')


@pytest.mark.parametrize('provider', ['apple', 'facebook', 'google'])
def test_create_federated_user_email_taken(user_manager, provider):
    # configure cognito to respond as if email is already taken
    user_id, username = str(uuid.uuid4()), str(uuid.uuid4())[:8]
    email = f'{username}@somedomain.com'
    user_manager.clients[provider].configure_mock(**{'get_verified_email.return_value': email})
    exception = user_manager.cognito_client.user_pool_client.exceptions.UsernameExistsException(
        {'Error': {'Code': '<code>', 'Message': 'An account with the email already exists.'}}, '<operation name>',
    )
    user_manager.cognito_client.user_pool_client.admin_create_user = mock.Mock(side_effect=exception)
    with pytest.raises(UserValidationException, match=f'Email `{email}` already taken'):
        user_manager.create_federated_user(provider, user_id, username, 'provider-token')


@pytest.mark.parametrize('provider', ['apple', 'facebook', 'google'])
def test_create_federated_user_invalid_token(user_manager, caplog, provider):
    provider_token = 'google-token'
    user_id = 'my-user-id'
    username = 'newuser'

    # set up our mocks to behave correctly
    user_manager.clients[provider].configure_mock(
        **{'get_verified_email.side_effect': ValueError('wrong flavor')}
    )

    # create the google user, check it is as expected
    with caplog.at_level(logging.WARNING):
        with pytest.raises(UserValidationException, match='wrong flavor'):
            user_manager.create_federated_user(provider, user_id, username, provider_token)
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == 'WARNING'
    assert 'wrong flavor' in caplog.records[0].msg


@pytest.mark.parametrize('provider', ['apple', 'facebook', 'google'])
def test_create_federated_user_cognito_identity_pool_exception_cleansup(user_manager, real_user, provider):
    user_id = 'my-user-id'

    # set up our mocks to behave correctly
    user_manager.clients[provider].configure_mock(**{'get_verified_email.return_value': 'me@email.com'})
    user_manager.cognito_client.create_verified_user_pool_entry = mock.Mock()
    user_manager.cognito_client.get_user_pool_id_token = mock.Mock(return_value='cog-token')
    user_manager.cognito_client.link_identity_pool_entries = mock.Mock(side_effect=Exception('anything bad'))
    user_manager.cognito_client.delete_user_pool_entry = mock.Mock()

    # create the user, check we tried to clean up after the failure
    with pytest.raises(Exception, match='anything bad'):
        user_manager.create_federated_user(provider, user_id, 'username', 'provider-token')
    assert user_manager.cognito_client.delete_user_pool_entry.mock_calls == [mock.call(user_id)]


def test_get_available_placeholder_photo_codes(user_manager):
    s3_client = user_manager.s3_placeholder_photos_client
    user_manager.placeholder_photos_directory = 'placeholder-photos'

    # check before we add any placeholder photos
    codes = user_manager.get_available_placeholder_photo_codes()
    assert codes == []

    # add a placeholder photo, check again
    path = 'placeholder-photos/black-white-cat/native.jpg'
    s3_client.put_object(path, b'placeholder', 'image/jpeg')
    codes = user_manager.get_available_placeholder_photo_codes()
    assert len(codes) == 1
    assert codes[0] == 'black-white-cat'

    # add another placeholder photo, check again
    path = 'placeholder-photos/orange-person/native.jpg'
    s3_client.put_object(path, b'placeholder', 'image/jpeg')
    path = 'placeholder-photos/orange-person/4k.jpg'
    s3_client.put_object(path, b'placeholder', 'image/jpeg')
    codes = user_manager.get_available_placeholder_photo_codes()
    assert len(codes) == 2
    assert codes[0] == 'black-white-cat'
    assert codes[1] == 'orange-person'


def test_get_random_placeholder_photo_code(user_manager):
    s3_client = user_manager.s3_placeholder_photos_client
    user_manager.placeholder_photos_directory = 'placeholder-photos'

    # check before we add any placeholder photos
    code = user_manager.get_random_placeholder_photo_code()
    assert code is None

    # add a placeholder photo, check again
    path = 'placeholder-photos/black-white-cat/native.jpg'
    s3_client.put_object(path, b'placeholder', 'image/jpeg')
    code = user_manager.get_random_placeholder_photo_code()
    assert code == 'black-white-cat'

    # add another placeholder photo, check again
    path = 'placeholder-photos/orange-person/native.jpg'
    s3_client.put_object(path, b'placeholder', 'image/jpeg')
    path = 'placeholder-photos/orange-person/4k.jpg'
    s3_client.put_object(path, b'placeholder', 'image/jpeg')
    code = user_manager.get_random_placeholder_photo_code()
    assert code in ['black-white-cat', 'orange-person']


def test_get_text_tags(user_manager, user1, user2):
    # no tags
    text = 'no tags here'
    assert user_manager.get_text_tags(text) == []

    # with tags, but not of users that exist
    text = 'hey @youDontExist and @meneither'
    assert user_manager.get_text_tags(text) == []

    # with tags, some that exist and others that dont
    text = f'hey @{user1.username} and @nopenope and @{user2.username}'
    assert sorted(user_manager.get_text_tags(text), key=lambda x: x['tag']) == sorted(
        [{'tag': f'@{user1.username}', 'userId': user1.id}, {'tag': f'@{user2.username}', 'userId': user2.id}],
        key=lambda x: x['tag'],
    )


def test_username_tag_regex(user_manager):
    reg = user_manager.username_tag_regex

    # no tags
    assert re.findall(reg, '') == []
    assert re.findall(reg, 'no tags here') == []

    # basic tags
    assert re.findall(reg, 'hi @you how @are @you') == ['@you', '@are', '@you']
    assert re.findall(reg, 'hi @y3o@m.e@ever_yone') == ['@y3o', '@m.e', '@ever_yone']

    # near misses
    assert re.findall(reg, 'too @34 @.. @go!forit @no-no') == []

    # uglies
    assert re.findall(reg, 'hi @._._ @4_. @A_A\n@B.4\r@333!?') == ['@._._', '@4_.', '@A_A', '@B.4', '@333']


def test_clear_expired_subscriptions(user_manager, user1, user2, user3):
    sub_duration = pendulum.duration(months=3)
    ms = pendulum.duration(microseconds=1)

    # grant these users subscriptions that expire at different times, verify
    now1 = pendulum.now('utc')
    user1.grant_subscription_bonus(now=now1)
    user2.grant_subscription_bonus(now=now1 + pendulum.duration(hours=1))
    user3.grant_subscription_bonus(now=now1 + pendulum.duration(hours=2))
    assert user1.refresh_item().item['subscriptionLevel']
    assert user2.refresh_item().item['subscriptionLevel']
    assert user3.refresh_item().item['subscriptionLevel']

    # test clear none
    assert user_manager.clear_expired_subscriptions() == 0
    assert user_manager.clear_expired_subscriptions(now=now1 + sub_duration - ms) == 0
    assert user1.refresh_item().item['subscriptionLevel']
    assert user2.refresh_item().item['subscriptionLevel']
    assert user3.refresh_item().item['subscriptionLevel']

    # test clear one of them
    assert user_manager.clear_expired_subscriptions(now=now1 + sub_duration) == 1
    assert 'subscriptionLevel' not in user1.refresh_item().item
    assert user2.refresh_item().item['subscriptionLevel']
    assert user3.refresh_item().item['subscriptionLevel']

    # test clear two of them
    assert user_manager.clear_expired_subscriptions(now=now1 + sub_duration + pendulum.duration(hours=2)) == 2
    assert 'subscriptionLevel' not in user1.refresh_item().item
    assert 'subscriptionLevel' not in user2.refresh_item().item
    assert 'subscriptionLevel' not in user3.refresh_item().item


def test_fire_gql_subscription_chats_with_unviewed_messages_count(user_manager):
    user_id = str(uuid.uuid4())
    user_item = {'chatsWithUnviewedMessagesCount': Decimal(34), 'otherField': 'anything'}
    with mock.patch.object(user_manager, 'appsync_client') as appsync_client_mock:
        user_manager.fire_gql_subscription_chats_with_unviewed_messages_count(user_id, user_item, 'unused')
    assert appsync_client_mock.mock_calls == [
        mock.call.fire_notification(
            user_id,
            GqlNotificationType.USER_CHATS_WITH_UNVIEWED_MESSAGES_COUNT_CHANGED,
            userChatsWithUnviewedMessagesCount=34,
        )
    ]
    # Decimals cause problems when serializing to JSON so make sure we've converted to int
    assert isinstance(
        appsync_client_mock.fire_notification.call_args.kwargs['userChatsWithUnviewedMessagesCount'], int
    )
