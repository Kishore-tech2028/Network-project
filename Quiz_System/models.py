from django.db import models

class Question(models.Model):
    question_text = models.CharField(max_length=255)
    answer = models.CharField(max_length=255)

class Option(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='options')
    option_text = models.CharField(max_length=255)
# Create your models here.
