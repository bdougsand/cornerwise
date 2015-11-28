define(["backbone", "project-view", "underscore"],
       function(B, ProjectView, _) {
           return B.View.extend({
               title: "Capital Projects",

               build: function() {
                   if (this._built) return;

                   var projects = this.collection;
                   this.listenTo(projects, "add", this.projectAdded)
                       .listenTo(projects, "reset", this.projectsReset)
                       .listenTo(projects, "change:selected", this.projectSelected);
                   this.$el.html("");
                   this.collection.each(_.bind(this.projectAdded, this));

                   this._built = true;
               },

               projectAdded: function(project) {
                   var view = new ProjectView({model: project});

                   this.$el.append(view.render().el);
               },

               render: function() {
                   this.build();
               }
           });
       });