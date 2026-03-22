from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("api/score/", views.get_live_score, name="api_score"),
    path("Quiz_System", views.home, name="quiz_system_home"),    path("Quiz_System/", views.home, name="quiz_system_home_slash"),
]
