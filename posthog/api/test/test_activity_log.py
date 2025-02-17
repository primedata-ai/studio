from typing import Any, Dict, Optional, Tuple, List

from rest_framework import status

from posthog.models import User
from posthog.test.base import APIBaseTest, QueryMatchingTest


def _feature_flag_json_payload(key: str) -> Dict:
    return {
        "key": key,
        "name": "",
        "filters": {"groups": [{"properties": [], "rollout_percentage": None}], "multivariate": None},
        "deleted": False,
        "active": True,
        "created_by": None,
        "is_simple_flag": False,
        "rollout_percentage": None,
        "ensure_experience_continuity": False,
        "experiment_set": None,
    }


class TestActivityLog(APIBaseTest, QueryMatchingTest):
    def setUp(self) -> None:
        super().setUp()
        self.other_user = User.objects.create_and_join(
            organization=self.organization,
            email="other_user@posthog.com",
            password="",
        )
        self.third_user = User.objects.create_and_join(
            organization=self.organization,
            email="third_user@posthog.com",
            password="",
        )

        # user one has created 10 insights and 2 flags
        # user two has edited them all
        # user three has edited most of them after that
        self._create_and_edit_things()

    def _create_and_edit_things(self):
        created_insights = []
        for _ in range(0, 11):
            insight_id, _ = self._create_insight({})
            created_insights.append(insight_id)

        flag_one = self.client.post(
            f"/api/projects/{self.team.id}/feature_flags/", _feature_flag_json_payload("one")
        ).json()["id"]

        flag_two = self.client.post(
            f"/api/projects/{self.team.id}/feature_flags/", _feature_flag_json_payload("two")
        ).json()["id"]

        notebook_json = self.client.post(
            f"/api/projects/{self.team.id}/notebooks/",
            {"content": "print('hello world')", "name": "notebook"},
        ).json()

        # other user now edits them
        self._edit_them_all(
            created_insights, flag_one, flag_two, notebook_json["short_id"], notebook_json["version"], self.other_user
        )
        # third user edits them
        self._edit_them_all(
            created_insights,
            flag_one,
            flag_two,
            notebook_json["short_id"],
            notebook_json["version"] + 1,
            self.third_user,
        )

        self.client.force_login(self.user)

    def _edit_them_all(
        self,
        created_insights: List[int],
        flag_one: str,
        flag_two: str,
        notebook_short_id: str,
        notebook_version: int,
        the_user: User,
    ) -> None:
        self.client.force_login(the_user)
        for created_insight_id in created_insights[:7]:
            update_response = self.client.patch(
                f"/api/projects/{self.team.id}/insights/{created_insight_id}",
                {"name": f"{created_insight_id}-insight-changed-by-{the_user.id}"},
            )
            self.assertEqual(update_response.status_code, status.HTTP_200_OK)
        assert (
            self.client.patch(
                f"/api/projects/{self.team.id}/feature_flags/{flag_one}", {"name": f"one-edited-by-{the_user.id}"}
            ).status_code
            == status.HTTP_200_OK
        )
        assert (
            self.client.patch(
                f"/api/projects/{self.team.id}/feature_flags/{flag_two}", {"name": f"two-edited-by-{the_user.id}"}
            ).status_code
            == status.HTTP_200_OK
        )
        assert (
            self.client.patch(
                f"/api/projects/{self.team.id}/notebooks/{notebook_short_id}",
                {"content": f"print('hello world again from {the_user.id}')", "version": notebook_version},
            ).status_code
            == status.HTTP_200_OK
        )

    def test_can_get_top_ten_important_changes(self) -> None:
        # user one is shown the most recent 10 of those changes
        self.client.force_login(self.user)
        changes = self.client.get(f"/api/projects/{self.team.id}/activity_log/important_changes")
        assert changes.status_code == status.HTTP_200_OK
        results = changes.json()["results"]
        assert len(results) == 10
        assert [c["scope"] for c in results] == [
            "Notebook",
            "FeatureFlag",
            "FeatureFlag",
            "Insight",
            "Insight",
            "Insight",
            "Insight",
            "Insight",
            "Insight",
            "Insight",
        ]
        assert [c["unread"] for c in results] == [True] * 10

    def test_can_get_top_ten_important_changes_including_my_edits(self) -> None:
        # user two is _also_ shown the most recent 10 of those changes
        # because they edited those things
        # and they were then changed
        self.client.force_login(self.other_user)
        changes = self.client.get(f"/api/projects/{self.team.id}/activity_log/important_changes")
        assert changes.status_code == status.HTTP_200_OK
        results = changes.json()["results"]
        assert [(c["user"]["id"], c["scope"]) for c in results] == [
            (
                self.third_user.pk,
                "Notebook",
            ),
            (
                self.third_user.pk,
                "FeatureFlag",
            ),
            (
                self.third_user.pk,
                "FeatureFlag",
            ),
            (
                self.third_user.pk,
                "Insight",
            ),
            (
                self.third_user.pk,
                "Insight",
            ),
            (
                self.third_user.pk,
                "Insight",
            ),
            (
                self.third_user.pk,
                "Insight",
            ),
            (
                self.third_user.pk,
                "Insight",
            ),
            (
                self.third_user.pk,
                "Insight",
            ),
            (
                self.third_user.pk,
                "Insight",
            ),
        ]
        assert [c["unread"] for c in results] == [True] * 10

    def test_reading_notifications_marks_them_unread(self):
        changes = self.client.get(f"/api/projects/{self.team.id}/activity_log/important_changes")
        assert changes.status_code == status.HTTP_200_OK

        most_recent_date = changes.json()["results"][2]["created_at"]

        # the user can mark where they have read up to
        bookmark_response = self.client.post(
            f"/api/projects/{self.team.id}/activity_log/bookmark_activity_notification", {"bookmark": most_recent_date}
        )
        assert bookmark_response.status_code == status.HTTP_204_NO_CONTENT

        changes = self.client.get(f"/api/projects/{self.team.id}/activity_log/important_changes")

        assert [c["unread"] for c in changes.json()["results"]] == [True, True]
        assert changes.json()["last_read"] == most_recent_date

    def _create_insight(
        self, data: Dict[str, Any], team_id: Optional[int] = None, expected_status: int = status.HTTP_201_CREATED
    ) -> Tuple[int, Dict[str, Any]]:
        if team_id is None:
            team_id = self.team.id

        if "filters" not in data:
            data["filters"] = {"events": [{"id": "$pageview"}]}

        response = self.client.post(f"/api/projects/{team_id}/insights", data=data)
        self.assertEqual(response.status_code, expected_status)

        response_json = response.json()
        return response_json.get("id", None), response_json
